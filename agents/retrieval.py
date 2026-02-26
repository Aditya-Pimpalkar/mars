"""
agents/retrieval.py
───────────────────
PHASE 2 ✅

Hybrid search (BM25 + keyword) over incidents-mars and runbooks-mars.
Each hit is converted into a claim and written to the Claim Ledger.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from es_client import get_client, check_connection
from claim_ledger.ledger import Claim, ClaimLedger
from agents.planner import Subtask

load_dotenv()

# Keywords that steer which index to search
INCIDENT_KEYWORDS = ["incident", "precedent", "past", "previous", "recurring", "history"]
RUNBOOK_KEYWORDS  = ["runbook", "procedure", "fix", "remediation", "mitigation", "steps", "how to"]


def _pick_index(subtask: Subtask, source_config: dict = None) -> str:
    desc = subtask.description.lower()

    if subtask.preferred_tool == "search_runbooks":
        return source_config.get("runbooks_index", "runbooks-mars") if source_config else "runbooks-mars"
    if subtask.preferred_tool == "search_incidents":
        return source_config.get("incidents_index", "incidents-mars") if source_config else "incidents-mars"

    # Fallback: infer from description
    if any(k in desc for k in RUNBOOK_KEYWORDS):
        return source_config.get("runbooks_index", "runbooks-mars") if source_config else "runbooks-mars"
    return source_config.get("incidents_index", "incidents-mars") if source_config else "incidents-mars"


def _build_query(subtask: Subtask, index: str) -> dict:
    """
    Simple but effective: multi_match across all text fields.
    Phase 3 can upgrade this to kNN + RRF when embeddings are added.
    """
    search_fields = (
        ["title", "steps", "tags", "category"]
        if "runbooks" in index
        else ["title", "summary", "root_cause", "tags"]
    )

    return {
        "size": 5,
        "query": {
            "multi_match": {
                "query": subtask.description,
                "fields": search_fields,
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        },
    }


def _hit_to_claim(hit: dict, index: str, session_id: str) -> Claim:
    """Convert an ES search hit into a Claim."""
    src = hit["_source"]
    score = hit["_score"]

    if "runbooks" in index:
        doc_id    = src.get("runbook_id", hit["_id"])
        title     = src.get("title", "Untitled runbook")
        content   = src.get("steps", "")[:300]
        updated   = src.get("last_updated")
        claim_text = (
            f"Runbook '{title}' (ID: {doc_id}) is relevant. "
            f"Key content: {content.strip()[:200]}..."
        )
    else:
        doc_id    = src.get("incident_id", hit["_id"])
        title     = src.get("title", "Untitled incident")
        content   = src.get("summary", src.get("root_cause", ""))
        updated   = src.get("created_at")
        summary = src.get('summary', '')
        root_cause = src.get('root_cause', 'unknown')
        claim_text = (
            f"Past incident '{title}' (ID: {doc_id}) is relevant. "
            f"Summary: {summary[:150]} "
            f"Root cause: {root_cause[:150]}"
        )

    # Confidence based on search relevance score (normalised)
    confidence = min(0.55 + (score / 10), 0.88)

    source_ts = None
    if updated:
        try:
            source_ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except Exception:
            pass

    return Claim(
        session_id=session_id,
        claim_text=claim_text,
        source_type="internal_doc",
        evidence_summary=f"Search hit: {title} | score={score:.2f} | index={index}",
        evidence_raw={
            "doc_id":    doc_id,
            "index":     index,
            "score":     score,
            "title":     title,
            "tags":      src.get("tags", []),
            "snippet":   content[:300],
        },
        source_timestamp=source_ts,
        confidence=round(confidence, 2),
        status="supported" if confidence >= 0.7 else "weakly_supported",
    )


def run(subtask: Subtask, session_id: str, ledger: ClaimLedger, source_config: dict = None) -> list[str]:
    """
    Search the appropriate index, write claims to ledger.
    Returns list of claim_ids written.
    """
    es    = get_client()
    index = _pick_index(subtask, source_config=source_config)
    query = _build_query(subtask, index)

    try:
        resp = es.search(index=index, body=query)
        hits = resp["hits"]["hits"]
    except Exception as e:
        print(f"  ⚠️   Search failed on {index}: {e}")
        return []

    if not hits:
        print(f"  ⚠️   No results found in {index} for: {subtask.description[:60]}")
        return []

    # Filter low-relevance hits for non-demo sources
    # Demo incidents score 2-4 on demo queries, but 0.3-0.7 on unrelated questions
    if source_config and source_config.get("metrics_index") != "metrics-mars":
        MIN_SCORE = 1.0
        hits = [h for h in hits if h["_score"] >= MIN_SCORE]
        if not hits:
            print(f"  ⚠️   No high-relevance results in {index} for: {subtask.description[:60]}")
            return []

    claim_ids = []
    for hit in hits:
        claim = _hit_to_claim(hit, index, session_id)
        cid   = ledger.write_claim(claim)
        claim_ids.append(cid)
        print(f"  ✅  [{index}] score={hit['_score']:.1f} → {claim.claim_text[:75]}...")

    return claim_ids

# ── Smoke test ─────────────────────────────────────────────────

if __name__ == "__main__":
    if not check_connection():
        sys.exit(1)

    es     = get_client()
    ledger = ClaimLedger(es)
    session_id = f"test_{uuid.uuid4().hex[:8]}"

    print(f"\n🔍  Running Retrieval smoke test (session: {session_id})\n")

    test_subtasks = [
        Subtask(
            id="s6",
            description="Search past incidents for similar API latency spike patterns and known root causes",
            preferred_tool="search_incidents",
            evidence_type="historical_precedent",
            stop_condition="similar incident found",
            priority=2,
        ),
        Subtask(
            id="s7",
            description="Look up runbooks for DB connection pool exhaustion diagnosis and rollback procedures",
            preferred_tool="search_runbooks",
            evidence_type="procedure",
            stop_condition="remediation steps found",
            priority=2,
        ),
    ]

    all_ids = []
    for subtask in test_subtasks:
        print(f"📋  Subtask {subtask.id}: {subtask.description[:70]}")
        ids = run(subtask, session_id, ledger)
        all_ids.extend(ids)
        print()

    print("─" * 60)
    summary = ledger.session_summary(session_id)
    print(f"\n📊  Session summary:")
    print(f"   Total claims written : {summary['total_claims']}")
    print(f"   Avg confidence       : {summary['avg_confidence']}")
    print(f"   Status breakdown     : {summary['status_breakdown']}")
    print(f"\n✅  Retrieval complete. {len(all_ids)} claims in ledger.\n")