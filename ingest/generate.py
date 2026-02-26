"""
ingest/generate.py
──────────────────
PHASE 1 ✅

Generates 30 days of realistic synthetic data and indexes it
into Elasticsearch. Plants a real incident on Jan 21, 2026:

  14:20 UTC — Deploy v2.4.1 pushed to production
  14:23 UTC — DB connection pool exhaustion begins
  14:23 UTC — latency_p99 spikes from ~80ms to 847ms
  14:31 UTC — Alert fires (8-min lag — good for demo)
  15:02 UTC — Rollback to v2.3.9 resolves the issue

Also generates intentional contradictions:
  - Old runbook says DB pool max = 50 (actual is 100) → Reviewer must resolve
  - Web source will claim spike started at 14:45 → ES|QL wins

Run: python ingest/generate.py
"""
from __future__ import annotations

import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from es_client import get_client, check_connection
from faker import Faker
from tqdm import tqdm

fake = Faker()

# ── Constants ──────────────────────────────────────────────────

START_DATE = datetime(2025, 12, 22, 0, 0, tzinfo=timezone.utc)
END_DATE = datetime(2026,  1, 21, 23, 59, tzinfo=timezone.utc)

INCIDENT_DATE = datetime(2026, 1, 21, tzinfo=timezone.utc)
SPIKE_START = datetime(2026, 1, 21, 14, 23, tzinfo=timezone.utc)
SPIKE_PEAK = datetime(2026, 1, 21, 14, 31, tzinfo=timezone.utc)
SPIKE_END = datetime(2026, 1, 21, 15,  2, tzinfo=timezone.utc)
DEPLOY_TIME = datetime(2026, 1, 21, 14, 20, tzinfo=timezone.utc)

SERVICES = ["api-gateway", "user-service", "payment-service", "db-proxy"]
REGIONS = ["us-east-1", "eu-west-1", "ap-southeast-1"]

# ── Helpers ────────────────────────────────────────────────────


def ts(dt: datetime) -> str:
    return dt.isoformat()


def jitter(val: float, pct: float = 0.1) -> float:
    return val * (1 + random.uniform(-pct, pct))


def in_spike(dt: datetime) -> bool:
    return SPIKE_START <= dt <= SPIKE_END


def spike_intensity(dt: datetime) -> float:
    """0.0 outside spike, 0.0–1.0 during spike (peaks at SPIKE_PEAK)."""
    if not in_spike(dt):
        return 0.0
    total = (SPIKE_END - SPIKE_START).total_seconds()
    elapsed = (dt - SPIKE_START).total_seconds()
    peak_offset = (SPIKE_PEAK - SPIKE_START).total_seconds()
    # triangle wave peaking at SPIKE_PEAK
    if elapsed <= peak_offset:
        return elapsed / peak_offset
    return 1.0 - (elapsed - peak_offset) / (total - peak_offset)


# ── Metrics ────────────────────────────────────────────────────

def generate_metrics(es) -> int:
    """1-minute resolution metrics for 30 days."""
    docs = []
    current = START_DATE
    while current <= END_DATE:
        intensity = spike_intensity(current)
        for region in REGIONS:
            # Only US-East is affected by the spike
            region_intensity = intensity if region == "us-east-1" else 0.0
            doc = {
                "@timestamp":      ts(current),
                "service":         "api-gateway",
                "region":          region,
                "latency_p50":     jitter(40 + region_intensity * 200),
                # peaks at 847
                "latency_p99":     jitter(80 + region_intensity * 767),
                "error_rate":      jitter(0.2 + region_intensity * 15),
                "rps":             jitter(1200),
                "db_pool_active":  int(jitter(20 + region_intensity * 80)),
                "db_pool_max":     100,
                "db_pool_wait_ms": jitter(5 + region_intensity * 400),
                "cpu_pct":         jitter(35 + region_intensity * 30),
                "mem_pct":         jitter(55),
            }
            docs.append({"index": {"_index": "metrics-mars"}})
            docs.append(doc)

        if len(docs) >= 500:
            es.bulk(body=docs)
            docs = []

        current += timedelta(minutes=1)

    if docs:
        es.bulk(body=docs)

    return int((END_DATE - START_DATE).total_seconds() / 60) * len(REGIONS)


# ── Logs ───────────────────────────────────────────────────────

NORMAL_MESSAGES = [
    "Request processed successfully",
    "Cache hit for key {key}",
    "DB query completed in {ms}ms",
    "Health check OK",
]

ERROR_MESSAGES = [
    "Connection pool exhausted — waiting for available connection",
    "DB connection timeout after {ms}ms",
    "Max retries exceeded for db-proxy",
    "Circuit breaker OPEN for db-proxy",
    "Request queued — pool at capacity ({n}/100)",
]


def generate_logs(es) -> int:
    """Sampled logs — higher volume during spike."""
    docs = []
    count = 0
    current = START_DATE

    while current <= END_DATE:
        intensity = spike_intensity(current)
        # More logs during spike, only for US-East
        n_logs = int(jitter(5 + intensity * 45))

        for _ in range(n_logs):
            if intensity > 0.3 and random.random() < intensity * 0.8:
                level = "ERROR"
                msg_tpl = random.choice(ERROR_MESSAGES)
                msg = msg_tpl.format(ms=int(jitter(5000)),
                                     n=int(20 + intensity * 80), key="x")
            else:
                level = "INFO"
                msg_tpl = random.choice(NORMAL_MESSAGES)
                msg = msg_tpl.format(ms=int(jitter(30)), key=fake.uuid4()[:8])

            doc = {
                "@timestamp":  ts(current + timedelta(seconds=random.randint(0, 59))),
                "service":     "api-gateway",
                "level":       level,
                "message":     msg,
                "trace_id":    fake.uuid4(),
                "latency_ms":  jitter(40 + intensity * 600),
                "status_code": 500 if level == "ERROR" else 200,
                "host":        f"api-gw-{random.randint(1, 4):02d}",
                "region":      "us-east-1",
                "env":         "production",
            }
            docs.append({"index": {"_index": "logs-mars"}})
            docs.append(doc)
            count += 1

        if len(docs) >= 1000:
            es.bulk(body=docs)
            docs = []

        current += timedelta(minutes=1)

    if docs:
        es.bulk(body=docs)

    return count


# ── Deployments ────────────────────────────────────────────────

def generate_deployments(es) -> int:
    deploys = [
        # Normal deploys before the incident
        {
            "@timestamp": ts(START_DATE + timedelta(days=3)),
            "version": "v2.3.7", "service": "api-gateway",
            "env": "production", "author": "alice",
            "status": "success", "rollback_of": None,
            "changes": "Performance improvements to request routing",
        },
        {
            "@timestamp": ts(START_DATE + timedelta(days=10)),
            "version": "v2.3.8", "service": "api-gateway",
            "env": "production", "author": "bob",
            "status": "success", "rollback_of": None,
            "changes": "Security patches and dependency updates",
        },
        {
            "@timestamp": ts(START_DATE + timedelta(days=18)),
            "version": "v2.3.9", "service": "api-gateway",
            "env": "production", "author": "alice",
            "status": "success", "rollback_of": None,
            "changes": "Minor bug fixes",
        },
        # ⚠️  THE INCIDENT DEPLOY
        {
            "@timestamp": ts(DEPLOY_TIME),
            "version": "v2.4.1", "service": "api-gateway",
            "env": "production", "author": "carol",
            "status": "success", "rollback_of": None,
            "changes": "New DB connection pool config — increased max_connections to 100, "
                       "reduced idle_timeout from 300s to 30s. WARNING: pool config change "
                       "not validated on production load profile.",
            "pipeline": "ci-main-243",
        },
        # ✅  ROLLBACK
        {
            "@timestamp": ts(SPIKE_END),
            "version": "v2.3.9", "service": "api-gateway",
            "env": "production", "author": "ops-bot",
            "status": "success", "rollback_of": "v2.4.1",
            "changes": "Emergency rollback — reverting v2.4.1 pool config change",
        },
    ]

    for d in deploys:
        es.index(index="deployments-mars", document=d)

    return len(deploys)


# ── Incidents ──────────────────────────────────────────────────

def generate_incidents(es) -> int:
    incidents = [
        # Jan 7 precedent — same root cause! (Reviewer will find this)
        {
            "incident_id":  "INC-1987",
            "title":        "API latency spike — DB pool exhaustion (Jan 7)",
            "summary":      "Production API latency spiked to 620ms p99 on January 7, 2026, "
                            "affecting the US-East region. Root cause was DB connection pool "
                            "exhaustion triggered by a misconfigured idle_timeout after deploy v2.3.6.",
            "root_cause":   "DB connection pool config change — idle_timeout set too low caused "
                            "rapid connection churn, exhausting the pool under production load.",
            "service":      "api-gateway",
            "severity":     "P1",
            "status":       "resolved",
            "created_at":   ts(datetime(2026, 1, 7, 11, 0, tzinfo=timezone.utc)),
            "resolved_at":  ts(datetime(2026, 1, 7, 12, 30, tzinfo=timezone.utc)),
            "duration_min": 90,
            "author":       "alice",
            "tags":         ["db", "connection-pool", "latency", "deploy-regression"],
        },
        # ⚠️  THE MAIN INCIDENT — intentionally slightly wrong timestamp (contradiction!)
        {
            "incident_id":  "INC-2041",
            "title":        "API latency spike — DB pool exhaustion (Jan 21)",
            "summary":      "Production API latency spiked to ~850ms p99 starting approximately "
                            "14:45 UTC on January 21, 2026 (US-East only). Deploy v2.4.1 at "
                            "14:20 UTC introduced a pool config change that caused exhaustion "
                            "under load. Rollback to v2.3.9 resolved the issue at 15:02 UTC.",
            "root_cause":   "DB connection pool exhaustion. v2.4.1 reduced idle_timeout causing "
                            "rapid connection recycling. Pool reached 100/100 active at peak load.",
            "service":      "api-gateway",
            "severity":     "P1",
            "status":       "resolved",
            # alert time
            "created_at":   ts(datetime(2026, 1, 21, 14, 31, tzinfo=timezone.utc)),
            "resolved_at":  ts(SPIKE_END),
            "duration_min": 39,
            "author":       "ops-oncall",
            "tags":         ["db", "connection-pool", "latency", "deploy-regression", "v2.4.1"],
        },
        # Background incidents for variety
        {
            "incident_id":  "INC-1901",
            "title":        "Payment service timeout — third-party API degraded",
            "summary":      "Payment processing timeouts caused by Stripe API degradation. "
                            "No internal root cause. Resolved when Stripe recovered.",
            "root_cause":   "External dependency (Stripe API) degradation.",
            "service":      "payment-service",
            "severity":     "P2",
            "status":       "resolved",
            "created_at":   ts(START_DATE + timedelta(days=5)),
            "resolved_at":  ts(START_DATE + timedelta(days=5, hours=2)),
            "duration_min": 120,
            "author":       "bob",
            "tags":         ["payment", "external-dependency", "timeout"],
        },
    ]

    for inc in incidents:
        # Note: embeddings are added in Phase 2 when we wire up the retrieval agent
        es.index(index="incidents-mars", id=inc["incident_id"], document=inc)

    return len(incidents)


# ── Runbooks ───────────────────────────────────────────────────

def generate_runbooks(es) -> int:
    runbooks = [
        # ⚠️  INTENTIONAL CONTRADICTION: says pool max = 50 (actual is 100)
        {
            "runbook_id":   "RB-0034",
            "title":        "DB Connection Pool Exhaustion — Diagnosis and Remediation",
            "service":      "api-gateway",
            "category":     "incident-response",
            "steps":        """
## Symptoms
- latency_p99 > 300ms sustained for > 2 minutes
- ERROR logs: "Connection pool exhausted"
- db_pool_active approaching maximum (pool max = 50)

## Diagnosis Steps
1. Check db_pool_active metric — if at 50/50, pool is exhausted
2. Check recent deploys for pool config changes
3. Check idle_timeout setting — should be >= 300s for production load

## Remediation
1. If caused by deploy: initiate rollback immediately
2. Run: kubectl rollout undo deployment/api-gateway
3. Verify latency_p99 drops below 100ms within 5 minutes
4. Post incident summary within 2 hours

## Prevention
- All pool config changes must be load-tested on staging first
- idle_timeout < 300s is a BLOCK for production deploys
""",
            "last_updated": ts(datetime(2025, 6, 1, tzinfo=timezone.utc)),  # OLD — outdated!
            "author":       "platform-team",
            "tags":         ["db", "connection-pool", "incident-response"],
        },
        {
            "runbook_id":   "RB-0041",
            "title":        "Emergency Rollback Procedure — api-gateway",
            "service":      "api-gateway",
            "category":     "deployment",
            "steps":        """
## When to use this runbook
Use when a production deploy causes a P1 or P2 incident and 
must be reverted immediately.

## Steps
1. Identify the last stable version from deployments-mars index
2. Trigger rollback: ops-bot rollback api-gateway --to <version>
3. Monitor: latency_p99 should drop within 3 minutes
4. Verify: error_rate returns to baseline (< 1%)
5. Page the oncall engineer with rollback confirmation
6. Open incident ticket referencing the failed deploy version

## Automated rollback
- ops-bot will auto-rollback if error_rate > 10% for > 5 minutes
- Manual override: ops-bot rollback --force
""",
            "last_updated": ts(datetime(2026, 1, 10, tzinfo=timezone.utc)),
            "author":       "platform-team",
            "tags":         ["rollback", "deployment", "incident-response"],
        },
        {
            "runbook_id":   "RB-0055",
            "title":        "Latency Spike Investigation Checklist",
            "service":      "api-gateway",
            "category":     "observability",
            "steps":        """
## Step 1 — Identify scope
- Which region? Check metrics-mars by region
- Which service? Check error_rate by service
- What time did it start? Use ES|QL: spike_window template

## Step 2 — Correlate with deploys
- Were any deploys in the 30 minutes before spike onset?
- Check deployments-mars for the window

## Step 3 — Check DB metrics
- db_pool_active vs db_pool_max — exhaustion?
- db_pool_wait_ms spike? Indicates pool contention

## Step 4 — Check for precedent
- Search incidents-mars for similar patterns
- Check tags: latency, connection-pool, deploy-regression

## Step 5 — Decide: rollback or hotfix?
- If deploy-correlated and P1: rollback first, investigate after
- Rollback procedure: see RB-0041
""",
            "last_updated": ts(datetime(2026, 1, 15, tzinfo=timezone.utc)),
            "author":       "alice",
            "tags":         ["latency", "observability", "investigation"],
        },
    ]

    for rb in runbooks:
        # Note: embeddings are added in Phase 2 when we wire up the retrieval agent
        es.index(index="runbooks-mars", id=rb["runbook_id"], document=rb)

    return len(runbooks)


# ── Main ───────────────────────────────────────────────────────

def run_ingest():
    if not check_connection():
        sys.exit(1)

    es = get_client()

    print("\n📥  Generating synthetic data for MARS demo...\n")
    print(f"  Scenario: API latency spike on Jan 21, 2026")
    print(f"  Deploy:   v2.4.1 at 14:20 UTC")
    print(f"  Spike:    14:23 – 15:02 UTC (US-East only)")
    print(f"  Peak:     847ms p99 at 14:31 UTC\n")

    tasks = [
        ("Metrics (30 days, 1-min resolution)", generate_metrics),
        ("Logs (sampled, spike has 10x volume)", generate_logs),
        ("Deployments (5 events incl. rollback)",  generate_deployments),
        ("Incidents (3 tickets incl. precedent)", generate_incidents),
        ("Runbooks (3 docs incl. contradiction)",  generate_runbooks),
    ]

    for label, fn in tasks:
        with tqdm(total=1, desc=f"  {label}", bar_format="{l_bar}{bar}| {elapsed}s") as bar:
            count = fn(es)
            bar.update(1)
        print(f"      → {count} documents indexed")

    # Refresh all indices
    try:
        es.indices.refresh(
            index="metrics-mars,logs-mars,deployments-mars,incidents-mars,runbooks-mars")
    except Exception:
        pass  # Serverless handles refresh automatically
    print("\n✅  Ingest complete. Key planted contradictions:")
    print("   • RB-0034 says pool max = 50  (actual is 100) — Reviewer must resolve")
    print("   • INC-2041 says spike started at 14:45 UTC  (ES|QL says 14:23) — ES|QL wins")
    print("\nReady to run the Planner: python agents/planner.py\n")


if __name__ == "__main__":
    run_ingest()
