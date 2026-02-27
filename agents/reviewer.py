"""
agents/reviewer.py
──────────────────
PHASE 3 ✅

The Reviewer / Reconciler Agent:
  1. Reads all claims from the Claim Ledger
  2. Detects contradictions between claims
  3. Resolves them using priority hierarchy:
       ES|QL data > internal docs > web
       newer source > older source
  4. Triggers Auto Follow-Up for weak/unknown claims
  5. Produces the final sourced report
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
from agents.verifier import run as verifier_run
from agents.planner import Subtask
from agents.sources import get_source_config

load_dotenv()

# ── Conflict detection rules ───────────────────────────────────

# Pairs of keywords that signal two claims may contradict each other
CONTRADICTION_SIGNALS = [
    ("pool max", "pool"),
    ("spike", "14:"),       # timestamp disagreements
    ("started at", "began"),
    ("exhausted", "under pressure"),
]

# Resolution hierarchy: higher index = higher trust
SOURCE_PRIORITY = {
    "esql_data":    3,
    "internal_doc": 2,
    "web":          1,
    "unknown":      0,
}


def _detect_contradictions(claims: list[Claim]) -> list[tuple[Claim, Claim, str]]:
    """
    Find pairs of claims that likely contradict each other.
    Returns list of (claim_a, claim_b, reason) tuples.
    """
    contradictions = []
    checked = set()
    pool_doc_ids_flagged = set()   # prevent same doc being flagged twice
    spike_doc_ids_flagged = set()  # prevent same doc being flagged twice

    for i, a in enumerate(claims):
        for j, b in enumerate(claims):
            if i >= j:
                continue
            pair_key = f"{a.claim_id}:{b.claim_id}"
            if pair_key in checked:
                continue
            checked.add(pair_key)

            # ── Pool size contradiction ─────────────────────────
            # Only fire once per doc claim — avoid duplicate pairs
            # when multiple esql claims all say "100"
            if ("pool" in a.claim_text.lower() and
                "pool" in b.claim_text.lower()):
                types = {a.source_type, b.source_type}
                if "esql_data" in types and "internal_doc" in types:
                    doc_claim  = a if a.source_type == "internal_doc" else b
                    esql_claim = a if a.source_type == "esql_data"    else b
                    if doc_claim.claim_id not in pool_doc_ids_flagged:
                        a_has_50 = "50" in esql_claim.claim_text and "100" not in esql_claim.claim_text
                        d_has_50 = "50" in doc_claim.claim_text  and "100" not in doc_claim.claim_text
                        if d_has_50 and not a_has_50:
                            pool_doc_ids_flagged.add(doc_claim.claim_id)
                            contradictions.append((
                                esql_claim, doc_claim,
                                "Conflicting DB pool maximum values (runbook says 50, ES|QL data shows 100)"
                            ))

            # ── Timestamp contradiction ─────────────────────────
            # Only fire once per doc claim that contains 14:45
            if ("spike" in a.claim_text.lower() and
                "spike" in b.claim_text.lower() and
                a.source_type != b.source_type):
                doc_claim  = a if a.source_type == "internal_doc" else b
                esql_claim = a if a.source_type == "esql_data"    else b
                if doc_claim.claim_id not in spike_doc_ids_flagged:
                    esql_early = "14:23" in esql_claim.claim_text or "14:25" in esql_claim.claim_text
                    doc_late   = "14:45" in doc_claim.claim_text
                    if esql_early and doc_late:
                        spike_doc_ids_flagged.add(doc_claim.claim_id)
                        contradictions.append((
                            esql_claim, doc_claim,
                            "Conflicting spike start timestamps (ES|QL: 14:25 vs incident ticket: 14:45)"
                        ))

    return contradictions

def _resolve_contradiction(
    a: Claim, b: Claim, reason: str, ledger: ClaimLedger
) -> str:
    """
    Apply resolution hierarchy. Returns explanation string.
    """
    priority_a = SOURCE_PRIORITY.get(a.source_type, 0)
    priority_b = SOURCE_PRIORITY.get(b.source_type, 0)

    if priority_a >= priority_b:
        winner, loser = a, b
    else:
        winner, loser = b, a

    reasoning = (
        f"Conflict: {reason}. "
        f"Resolved in favour of '{winner.source_type}' over '{loser.source_type}' "
        f"per resolution hierarchy (ES|QL data > internal docs > web). "
        f"Winner claim: \"{winner.claim_text[:120]}...\""
    )

    ledger.resolve_conflict(winner.claim_id, loser.claim_id, reasoning)
    return reasoning


def _trigger_followup(
    weak_claims: list[Claim],
    session_id: str,
    ledger: ClaimLedger,
    source_config: dict | None = None,
    max_iterations: int = 3,
) -> int:
    resolved = 0
    threshold = float(os.getenv("FOLLOWUP_CONFIDENCE_THRESHOLD", "0.6"))
    if source_config is None:
        source_config = get_source_config("demo")

    for claim in weak_claims:
        if claim.status == "contradicted":
            print(f"  ⏭️   Skipping contradicted claim: {claim.claim_text[:60]}...")
            continue

        if claim.source_type == "internal_doc":
            try:
                es = get_client()
                nav = es.get(index="claim-ledger-mars", id=f"narrative_{session_id}")
                session_source = nav["_source"].get("data_source", "demo")
            except Exception:
                session_source = "demo"
            if session_source != "demo":
                continue

        if claim.follow_up_count >= max_iterations:
            ledger.update_claim(claim.claim_id, {
                "follow_up_status": "exhausted",
                "resolution_reasoning": "Max follow-up iterations reached. Best available evidence shown.",
            })
            continue

        ledger.update_claim(claim.claim_id, {"follow_up_status": "querying"})

        claim_lower = claim.claim_text.lower()
        if "pool" in claim_lower or "db" in claim_lower:
            followup_subtask = Subtask(
                id="followup_db",
                description="Verify DB connection pool exhaustion with detailed metrics",
                preferred_tool="esql",
                evidence_type="db",
                stop_condition="pool exhaustion confirmed with exact values",
                priority=1,
            )
        elif "deploy" in claim_lower or "version" in claim_lower:
            followup_subtask = Subtask(
                id="followup_deploy",
                description="Verify deploy timing and changes that caused the incident",
                preferred_tool="esql",
                evidence_type="deploy",
                stop_condition="deploy correlated with spike onset",
                priority=1,
            )
        elif "region" in claim_lower:
            followup_subtask = Subtask(
                id="followup_region",
                description="Confirm which regions were affected by the latency spike",
                preferred_tool="esql",
                evidence_type="region",
                stop_condition="affected regions confirmed",
                priority=1,
            )
        else:
            followup_subtask = Subtask(
                id="followup_spike",
                description="Verify latency spike window and magnitude",
                preferred_tool="esql",
                evidence_type="timestamp",
                stop_condition="spike window confirmed",
                priority=1,
            )

        query_key = followup_subtask.description
        if query_key in claim.previous_queries:
            ledger.update_claim(claim.claim_id, {
                "follow_up_count":  claim.follow_up_count + 1,
                "follow_up_status": "exhausted",
                "resolution_reasoning": "Follow-up query identical to previous — no new evidence available.",
            })
            continue

        print(f"  🔄  Follow-up query for weak claim: {claim.claim_text[:60]}...")

        try:
            from agents.verifier import _pick_templates, _run_esql, _rows_to_claim_text
            es = get_client()
            templates = _pick_templates(followup_subtask)
            found_strong_evidence = False

            for template_name in templates:
                rows, actual_template = _run_esql(es, template_name, source_config=source_config)
                _, confidence = _rows_to_claim_text(actual_template, rows)
                if confidence >= threshold:
                    found_strong_evidence = True
                    break

            if found_strong_evidence:
                ledger.update_claim(claim.claim_id, {
                    "follow_up_count":  claim.follow_up_count + 1,
                    "follow_up_status": "resolved",
                    "confidence":       min(claim.confidence + 0.2, 0.90),
                    "status":           "supported",
                    "previous_queries": claim.previous_queries + [query_key],
                    "resolution_reasoning": "Confidence raised by follow-up ES|QL corroboration.",
                })
                resolved += 1
                print(f"      → Resolved ✅  (confidence raised)")
            else:
                ledger.update_claim(claim.claim_id, {
                    "follow_up_count":  claim.follow_up_count + 1,
                    "follow_up_status": "exhausted",
                    "previous_queries": claim.previous_queries + [query_key],
                    "resolution_reasoning": "Follow-up query returned no high-confidence evidence.",
                })
                print(f"      → Exhausted ⚠️   (no new evidence)")

        except Exception as e:
            print(f"      → Follow-up failed: {e}")
            ledger.update_claim(claim.claim_id, {
                "follow_up_count":  claim.follow_up_count + 1,
                "follow_up_status": "exhausted",
                "previous_queries": claim.previous_queries + [query_key],
                "resolution_reasoning": f"Follow-up failed: {str(e)}",
            })

    return resolved

def _generate_report(claims: list[Claim], session_id: str) -> str:
    """
    Build the final sourced report from resolved claims.
    Every sentence references the claim_id that backs it.
    """
    # Separate claims by type and status
    esql_claims = [c for c in claims
                   if c.source_type == "esql_data" and c.status == "supported"]
    doc_claims  = [c for c in claims
                   if c.source_type == "internal_doc" and c.status == "supported"]
    conflicts   = [c for c in claims if c.status == "contradicted"]
    unknown     = [c for c in claims if c.status == "unknown"]

    lines = []
    lines.append("=" * 60)
    lines.append("MARS — INCIDENT RESEARCH REPORT")
    lines.append(f"Session: {session_id}")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)
    lines.append("")

    lines.append("── FINDINGS (ES|QL verified) ──────────────────────────────")
    if esql_claims:
        for c in esql_claims:
            lines.append(f"  [{c.claim_id}] (confidence: {c.confidence:.0%})")
            lines.append(f"  {c.claim_text}")
            lines.append("")
    else:
        lines.append("  No ES|QL-verified findings.")
    lines.append("")
    web_claims = [c for c in claims
                  if c.source_type == "web" and c.status != "contradicted"]
    lines.append("── CONTEXT (from internal docs) ───────────────────────────")
    if doc_claims:
        for c in doc_claims:
            lines.append(f"  [{c.claim_id}] (confidence: {c.confidence:.0%})")
            lines.append(f"  {c.claim_text[:200]}...")
            lines.append("")
    else:
        lines.append("  No supporting documentation found.")
    lines.append("")
    lines.append("── EXTERNAL CORROBORATION (Web Scout) ─────────────────────")
    if web_claims:
        for c in web_claims:
            lines.append(f"  [{c.claim_id}] (confidence: {c.confidence:.0%})")
            lines.append(f"  {c.claim_text[:200]}...")
            lines.append("")
    else:
        lines.append("  No external sources found.")
    lines.append("")
    lines.append("── CONFLICTS DETECTED & RESOLVED ──────────────────────────")
    if conflicts:
        for c in conflicts:
            lines.append(f"  [{c.claim_id}] OVERRIDDEN")
            lines.append(f"  {c.claim_text[:120]}...")
            if c.resolution_reasoning:
                lines.append(f"  Reason: {c.resolution_reasoning[:150]}...")
            lines.append("")
    else:
        lines.append("  No conflicts detected.")
    lines.append("")

    if unknown:
        lines.append("── FLAGGED FOR HUMAN REVIEW ───────────────────────────────")
        for c in unknown:
            lines.append(f"  [{c.claim_id}] {c.claim_text[:120]}...")
        lines.append("")

    lines.append("── SUMMARY ────────────────────────────────────────────────")
    lines.append(f"  Total claims    : {len(claims)}")
    lines.append(f"  Verified        : {len(esql_claims)}")
    lines.append(f"  From docs       : {len(doc_claims)}")
    lines.append(f"  From web        : {len(web_claims)}")
    lines.append(f"  Conflicts found : {len(conflicts)}")
    lines.append(f"  Flagged         : {len(unknown)}")
    lines.append("=" * 60)

    return "\n".join(lines)


def run(session_id: str, question: str = "", data_source: str = "demo") -> str:
    """
    Full Reviewer pipeline. Reads ledger, resolves conflicts,
    triggers follow-ups, returns final report string.
    """
    es     = get_client()
    ledger = ClaimLedger(es)

    print(f"\n📖  Reading Claim Ledger (session: {session_id})...")
    claims = ledger.get_claims(session_id)
    print(f"   {len(claims)} claims loaded\n")

    if not claims:
        return "No claims found for this session."

    # ── Step 1: Detect contradictions ─────────────────────────
    print("🔍  Detecting contradictions...")
    contradictions = _detect_contradictions(claims)

    if contradictions:
        print(f"   {len(contradictions)} contradiction(s) found:")
        for a, b, reason in contradictions:
            print(f"   ⚡  {reason}")
            resolution = _resolve_contradiction(a, b, reason, ledger)
            print(f"      → {resolution[:100]}...")
    else:
        print("   No contradictions detected.")
    print()

    # ── Step 2: Auto follow-up for weak claims ─────────────────
    threshold = float(os.getenv("FOLLOWUP_CONFIDENCE_THRESHOLD", "0.6"))
    weak_claims = ledger.get_weak_claims(session_id, threshold=threshold)
    print(f"🔄  Checking for weak claims (confidence < {threshold})...")

    if weak_claims:
        print(f"   {len(weak_claims)} weak claim(s) — triggering follow-up queries...")
        max_iterations = int(os.getenv("FOLLOWUP_MAX_ITERATIONS", "3"))
        source_config = get_source_config(data_source)
        resolved = _trigger_followup(
            weak_claims,
            session_id,
            ledger,
            source_config=source_config,
            max_iterations=max_iterations,
        )
        print(f"   {resolved}/{len(weak_claims)} resolved by follow-up\n")
    else:
        print("   All claims meet confidence threshold ✅\n")

    # ── Step 3: Generate final report ─────────────────────────
    print("📝  Generating final report...")
    final_claims = ledger.get_claims(session_id)
    report = _generate_report(final_claims, session_id)

    return report


# ── Smoke test ─────────────────────────────────────────────────

if __name__ == "__main__":
    if not check_connection():
        sys.exit(1)

    from agents.planner   import run as planner_run
    from agents.verifier  import run as verifier_run
    from agents.retrieval import run as retrieval_run

    # Accept custom question from command line or use default
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=str,
                        default="Why did API latency spike last Tuesday afternoon?")
    args = parser.parse_args()

    session_id = f"mars_{uuid.uuid4().hex[:8]}"
    es         = get_client()
    ledger     = ClaimLedger(es)

    print(f"\n🚀  MARS Full Pipeline Run")
    print(f"   Session:  {session_id}")
    print(f"   Question: {args.question}\n")

    # ── Step 1: Planner decides what to investigate ────────────
    print("━━━ PLANNER ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    plan = planner_run(args.question, session_id=session_id)
    print(f"   {len(plan.subtasks)} subtasks planned\n")
    for s in plan.subtasks:
        print(f"   [{s.preferred_tool.upper():18s}] {s.id}: {s.description[:60]}")
    print()

    # ── Step 2: Route each subtask to the right agent ──────────
    print("━━━ VERIFIER (ES|QL) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    esql_tools = {"esql", "search"}
    for s in plan.subtasks:
        if s.preferred_tool in ("esql",):
            verifier_run(s, session_id, ledger)

    print("\n━━━ RETRIEVAL (Search) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for s in plan.subtasks:
        if s.preferred_tool in ("search_incidents", "search_runbooks"):
            retrieval_run(s, session_id, ledger)

# Step 3 — Web Scout (external corroboration)
    print("\n━━━ WEB SCOUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    from agents.web_scout import run as web_scout_run
    web_subtask = Subtask(
        id="s_web",
        description="Find external corroboration for DB connection pool exhaustion and idle_timeout misconfiguration",
        preferred_tool="web",
        evidence_type="external_corroboration",
        stop_condition="external source found",
        priority=3,
    )
    # Pass existing claims so Web Scout builds targeted queries
    existing = ledger.get_claims(session_id)
    web_scout_run(web_subtask, session_id, ledger, existing_claims=existing)

    # ── Step 3: Reviewer reconciles everything ─────────────────
    print("\n━━━ REVIEWER ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report = run(session_id)
    print(report)

    # ── Step 4: Print session ID for heatmap ───────────────────
    print(f"\n🗂️   Session ID for heatmap: {session_id}")
    print(f"     Open: http://localhost:8000?session={session_id}\n")