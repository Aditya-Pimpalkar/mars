"""
agents/verifier.py
──────────────────
PHASE 2 ✅

The Data Verifier Agent — the "truth anchor" of MARS.
Executes ES|QL queries against time-series indices and writes
numeric facts as high-confidence claims to the Claim Ledger.

ES|QL always wins conflict resolution over docs or web.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from es_client import get_client, check_connection
from claim_ledger.ledger import Claim, ClaimLedger
from agents.planner import Subtask
from agents.sources import DATA_SOURCES, get_source_config

load_dotenv()

# ── ES|QL Templates ────────────────────────────────────────────

ESQL_TEMPLATES = {
    "spike_window": """
        FROM metrics-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | STATS max_p99 = MAX(latency_p99), avg_p99 = AVG(latency_p99)
              BY bucket = DATE_TRUNC(1 minute, @timestamp)
        | WHERE max_p99 > {threshold}
        | SORT bucket ASC
        | LIMIT 50
    """,

    "deploy_lookup": """
        FROM deployments-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | KEEP @timestamp, version, service, author, status, changes, rollback_of
        | SORT @timestamp ASC
        | LIMIT 100
    """,

    "error_rate": """
        FROM logs-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | WHERE level == "ERROR"
        | STATS error_count = COUNT(*)
              BY bucket = DATE_TRUNC(1 minute, @timestamp)
        | SORT bucket ASC
        | LIMIT 50
    """,

    "db_pool": """
        FROM metrics-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | STATS max_active = MAX(db_pool_active),
                max_wait   = MAX(db_pool_wait_ms),
                max_pool   = MAX(db_pool_max)
              BY bucket = DATE_TRUNC(1 minute, @timestamp)
        | SORT bucket ASC
        | LIMIT 50
    """,

    "peak_latency": """
        FROM metrics-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | STATS peak = MAX(latency_p99),
                region_peak = MAX(latency_p99)
              BY region
        | SORT peak DESC
    """,

    "region_scope": """
        FROM metrics-mars
        | WHERE @timestamp >= "{start}" AND @timestamp <= "{end}"
        | WHERE latency_p99 > {threshold}
        | STATS affected_minutes = COUNT(*)
              BY region
        | SORT affected_minutes DESC
    """,

    # ── Sample web logs ────────────────────────────────────────
    "weblogs_spike": """
        FROM kibana_sample_data_logs
        | WHERE @timestamp >= now() - 7 days
        | STATS
            total_requests = COUNT(*),
            avg_bytes      = AVG(bytes),
            max_bytes      = MAX(bytes)
            BY bucket = DATE_TRUNC(6 hours, @timestamp)
        | SORT bucket ASC
        | LIMIT 50
    """,

    # ── Sample eCommerce ───────────────────────────────────────
    "ecommerce_summary": """
        FROM kibana_sample_data_ecommerce
        | WHERE order_date >= now() - 30 days
        | STATS
            total_orders  = COUNT(*),
            total_revenue = SUM(taxful_total_price),
            avg_order     = AVG(taxful_total_price),
            max_order     = MAX(taxful_total_price),
            total_items   = SUM(total_quantity)
            BY bucket = DATE_TRUNC(1 day, order_date)
        | SORT bucket ASC
        | LIMIT 50
    """,
}

# Maps subtask evidence_type keywords → which templates to run
EVIDENCE_TYPE_MAP = {
    "timestamp":        ["spike_window", "db_pool"],
    "numeric_metric":   ["spike_window", "peak_latency"],
    "deploy":           ["deploy_lookup"],
    "error":            ["error_rate"],
    "db":               ["db_pool"],
    "region":           ["region_scope"],
    "infrastructure":   ["db_pool", "error_rate"],
    "dependency":       ["db_pool"],
    "weblogs":          ["spike_window"],   # routes to weblogs_spike via source_config
    "ecommerce":        ["spike_window"],   # routes to ecommerce_summary via source_config
    "default":          ["spike_window", "deploy_lookup"],
}

INCIDENT_START    = "2026-01-21T13:30:00Z"
INCIDENT_END      = "2026-01-21T16:00:00Z"
LATENCY_THRESHOLD = 200


def _pick_templates(subtask: Subtask) -> list[str]:
    evidence = subtask.evidence_type.lower()
    for keyword, templates in EVIDENCE_TYPE_MAP.items():
        if keyword in evidence:
            return templates
    return EVIDENCE_TYPE_MAP["default"]


def _run_esql(es, template_name: str, source_config: dict = None) -> tuple[list[dict], str]:
    """
    Execute a named ES|QL template.
    Returns (rows, actual_template_name) so callers know which template ran.
    """
    if source_config is None:
        source_config = DATA_SOURCES["demo"]

    metrics_index = source_config.get("metrics_index", "metrics-mars")

    # Skip deploy_lookup for non-demo sources — no deployment data
    if template_name == "deploy_lookup" and metrics_index != "metrics-mars":
        return [], template_name

    # Route to correct template based on data source
    if metrics_index == "kibana_sample_data_logs":
        actual_template = "weblogs_spike"
    elif metrics_index == "kibana_sample_data_ecommerce":
        actual_template = "ecommerce_summary"
    else:
        actual_template = template_name

    template = ESQL_TEMPLATES.get(actual_template, ESQL_TEMPLATES.get(template_name, ""))
    if not template:
        return [], actual_template

    query = template.format(
        start=source_config.get("query_start", INCIDENT_START),
        end=source_config.get("query_end",     INCIDENT_END),
        threshold=source_config.get("threshold", LATENCY_THRESHOLD),
    ).strip()

    resp = es.esql.query(body={"query": query})

    columns = [col["name"] for col in resp["columns"]]
    rows = []
    for row in resp.get("values", resp.get("rows", [])):
        rows.append(dict(zip(columns, row)))
    return rows, actual_template


def _rows_to_claim_text(template_name: str, rows: list[dict]) -> tuple[str, float]:
    """Convert ES|QL result rows into a human-readable claim + confidence."""
    if not rows:
        return "No data found for this query.", 0.1

    if template_name == "spike_window":
        first = rows[0]
        peak  = max(r["max_p99"] for r in rows)
        start_bucket = first.get("bucket", "unknown")
        return (
            f"Latency spike detected: p99 exceeded {LATENCY_THRESHOLD}ms "
            f"starting at {start_bucket}, peaking at {peak:.0f}ms. "
            f"Spike lasted {len(rows)} minutes.",
            0.95,
        )

    if template_name == "peak_latency":
        peak_row = rows[0]
        return (
            f"Peak latency was {peak_row.get('peak', '?'):.0f}ms p99 "
            f"in region {peak_row.get('region', '?')}.",
            0.95,
        )

    if template_name == "deploy_lookup":
        deploys = [
            f"{r.get('version', '?')} by {r.get('author', '?')} "
            f"at {r.get('@timestamp', '?')}"
            for r in rows
        ]
        return (
            f"{len(rows)} deployment(s) found in the incident window: "
            + "; ".join(deploys) + ".",
            0.90,
        )

    if template_name == "error_rate":
        total_errors = sum(r.get("error_count", 0) for r in rows)
        peak_errors  = max(r.get("error_count", 0) for r in rows)
        return (
            f"Error rate spike: {total_errors} total errors across "
            f"{len(rows)} minutes, peak {peak_errors} errors/min.",
            0.92,
        )

    if template_name == "db_pool":
        peak_active = max(r.get("max_active", 0) for r in rows)
        peak_wait   = max(r.get("max_wait",   0) for r in rows)
        pool_max    = rows[0].get("max_pool", "?")
        return (
            f"DB connection pool: peaked at {peak_active}/{pool_max} active "
            f"connections with max wait time {peak_wait:.0f}ms — "
            f"{'pool exhausted' if peak_active >= 95 else 'pool under pressure'}.",
            0.95,
        )

    if template_name == "region_scope":
        affected = [r.get("region", "?") for r in rows if r.get("affected_minutes", 0) > 0]
        return (
            f"Latency spike affected {len(affected)} region(s): "
            + ", ".join(affected) + ".",
            0.90,
        )

    if template_name == "weblogs_spike":
        total      = sum(r.get("total_requests", 0) for r in rows)
        peak       = max(r.get("total_requests", 0) for r in rows)
        peak_bytes = max(r.get("max_bytes", 0) for r in rows)
        return (
            f"Web traffic analysis: {total:,} total requests over {len(rows)} "
            f"6-hour windows. Peak traffic: {peak} requests/window. "
            f"Max response size: {peak_bytes:,} bytes.",
            0.92,
        )

    if template_name == "ecommerce_summary":
        total_orders  = sum(r.get("total_orders",  0) for r in rows)
        total_revenue = sum(r.get("total_revenue", 0) for r in rows)
        avg_order     = total_revenue / total_orders if total_orders else 0
        peak_revenue  = max(r.get("total_revenue", 0) for r in rows)
        return (
            f"eCommerce analysis: {total_orders:,} orders generating "
            f"${total_revenue:,.2f} total revenue over {len(rows)} days. "
            f"Average order value: ${avg_order:.2f}. "
            f"Peak daily revenue: ${peak_revenue:,.2f}.",
            0.92,
        )

    # Fallback
    return f"ES|QL returned {len(rows)} rows for {template_name}.", 0.7


def run(subtask: Subtask, session_id: str, ledger: ClaimLedger, source_config: dict = None) -> list[str]:
    """
    Execute ES|QL queries for a subtask, write claims to ledger.
    Returns list of claim_ids written.
    """
    if source_config is None:
        source_config = DATA_SOURCES["demo"]

    es        = get_client()
    templates = _pick_templates(subtask)
    claim_ids = []

    for template_name in templates:
        try:
            rows, actual_template = _run_esql(es, template_name, source_config=source_config)

            # Skip — no data returned, don't write empty claim
            if not rows:
                continue

            claim_text, confidence = _rows_to_claim_text(actual_template, rows)

            claim = Claim(
                session_id=session_id,
                claim_text=claim_text,
                source_type="esql_data",
                evidence_summary=f"ES|QL template: {actual_template} | {len(rows)} rows",
                evidence_raw={
                    "template":     actual_template,
                    "query_window": f"{source_config.get('query_start')} to {source_config.get('query_end')}",
                    "row_count":    len(rows),
                    "sample_rows":  rows[:3],
                },
                source_timestamp=datetime.now(timezone.utc),
                confidence=confidence,
                status="supported" if confidence >= 0.8 else "weakly_supported",
            )

            cid = ledger.write_claim(claim)
            claim_ids.append(cid)
            print(f"  ✅  [{actual_template}] → {claim_text[:80]}...")

        except Exception as e:
            print(f"  ⚠️   [{template_name}] failed: {e}")

    return claim_ids


# ── Smoke test ─────────────────────────────────────────────────

if __name__ == "__main__":
    if not check_connection():
        sys.exit(1)

    es     = get_client()
    ledger = ClaimLedger(es)
    session_id = f"test_{uuid.uuid4().hex[:8]}"

    print(f"\n🔍  Running Verifier smoke test (session: {session_id})\n")

    test_subtasks = [
        Subtask(id="s1", description="Identify latency spike window",
                preferred_tool="esql", evidence_type="timestamp",
                stop_condition="spike window found", priority=1),
        Subtask(id="s2", description="Check error rate during spike",
                preferred_tool="esql", evidence_type="error",
                stop_condition="error rate quantified", priority=1),
        Subtask(id="s3", description="Check DB pool metrics",
                preferred_tool="esql", evidence_type="db",
                stop_condition="pool exhaustion confirmed", priority=1),
        Subtask(id="s4", description="Find deploys in window",
                preferred_tool="esql", evidence_type="deploy",
                stop_condition="deploy found", priority=1),
        Subtask(id="s5", description="Check region scope",
                preferred_tool="esql", evidence_type="region",
                stop_condition="affected regions identified", priority=2),
    ]

    all_claim_ids = []
    for subtask in test_subtasks:
        print(f"📋  Subtask {subtask.id}: {subtask.description}")
        ids = run(subtask, session_id, ledger)
        all_claim_ids.extend(ids)
        print()

    print("─" * 60)
    summary = ledger.session_summary(session_id)
    print(f"\n📊  Session summary:")
    print(f"   Total claims written : {summary['total_claims']}")
    print(f"   Avg confidence       : {summary['avg_confidence']}")
    print(f"   Status breakdown     : {summary['status_breakdown']}")
    print(f"\n✅  Verifier complete. {len(all_claim_ids)} claims in ledger.\n")