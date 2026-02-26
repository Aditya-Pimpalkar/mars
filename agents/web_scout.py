"""
agents/web_scout.py
───────────────────

The Web Scout Agent searches the web for external corroboration
of claims found by the Verifier and Retrieval agents.

Use cases:
- Confirm known issues with specific library versions
- Find public CVEs or changelogs related to the incident
- Corroborate DB connection pool behavior under load
- Find similar incidents reported publicly

Trust level: LOWEST — web sources are always overridden by
ES|QL data and internal docs in conflict resolution.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from es_client import get_client, check_connection
from claim_ledger.ledger import Claim, ClaimLedger
from agents.planner import Subtask

load_dotenv()


def _search_web(query: str, max_results: int = 3) -> list[dict]:
    """Execute a web search via Tavily."""
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        print("  ⚠️   TAVILY_API_KEY not set — skipping web search")
        return []

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        resp   = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        return resp.get("results", [])
    except Exception as e:
        print(f"  ⚠️   Web search failed: {e}")
        return []


def _build_query(claim_text: str, question: str) -> str:
    """
    Build a focused web search query from a claim.
    Keep it specific to get useful corroboration results.
    """
    # Extract key technical terms from the claim
    keywords = []

    if "connection pool" in claim_text.lower():
        keywords.append("database connection pool exhaustion production")
    if "idle_timeout" in claim_text.lower():
        keywords.append("idle_timeout misconfiguration latency spike")
    if "deploy" in claim_text.lower() or "v2.4" in claim_text.lower():
        keywords.append("deployment configuration change latency regression")
    if "latency" in claim_text.lower() and "spike" in claim_text.lower():
        keywords.append("API latency spike root cause connection pool")

    if keywords:
        return keywords[0]

    # Fallback: use first 60 chars of claim as query
    return claim_text[:60] + " incident root cause"


def run(subtask: Subtask, session_id: str, ledger: ClaimLedger,
        existing_claims: list[Claim] | None = None) -> list[str]:
    """
    Search the web for external corroboration of existing claims.
    Only runs when TAVILY_API_KEY is set.
    Returns list of claim_ids written.
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        print("  ⏭️   Web Scout skipped — no TAVILY_API_KEY in .env")
        return []

    # Use existing claims to build targeted queries
    # Focus on the highest-confidence ES|QL claims
    if existing_claims:
        esql_claims = [
            c for c in existing_claims
            if c.source_type == "esql_data" and c.status == "supported"
        ]
        target_claims = esql_claims[:2]  # top 2 ES|QL claims
    else:
        target_claims = []

    if not target_claims:
        # Fallback: search based on subtask description
        query = subtask.description
        target_claims = [None]

    claim_ids = []

    for target in target_claims:
        query = _build_query(
            target.claim_text if target else subtask.description,
            subtask.description
        )

        print(f"  🌐  Web search: {query[:60]}...")
        results = _search_web(query, max_results=3)

        if not results:
            continue

        for result in results:
            title   = result.get("title", "Unknown")
            url     = result.get("url", "")
            content = result.get("content", "")[:300]
            score   = result.get("score", 0.5)

            if not content:
                continue

            claim_text = (
                f"External source '{title}' corroborates: "
                f"{content[:200]}... "
                f"[Source: {url[:60]}]"
            )

            # Web confidence is always lower than internal sources
            confidence = min(0.4 + (score * 0.3), 0.65)

            claim = Claim(
                session_id=session_id,
                claim_text=claim_text,
                source_type="web",
                evidence_summary=f"Web search: {query[:60]} | score={score:.2f}",
                evidence_raw={
                    "title":   title,
                    "url":     url,
                    "content": content,
                    "score":   score,
                    "query":   query,
                },
                source_timestamp=datetime.now(timezone.utc),
                confidence=round(confidence, 2),
                status="weakly_supported",
            )

            cid = ledger.write_claim(claim)
            claim_ids.append(cid)
            print(f"  ✅  [web] {title[:60]}... (conf: {confidence:.0%})")

    return claim_ids


# ── Smoke test ─────────────────────────────────────────────

if __name__ == "__main__":
    import uuid
    if not check_connection():
        sys.exit(1)

    es     = get_client()
    ledger = ClaimLedger(es)
    session_id = f"test_{uuid.uuid4().hex[:8]}"

    print(f"\n🌐  Running Web Scout smoke test (session: {session_id})\n")

    test_subtask = Subtask(
        id="s_web",
        description="Find external corroboration for DB connection pool exhaustion and idle_timeout misconfiguration causing latency spikes",
        preferred_tool="web",
        evidence_type="external_corroboration",
        stop_condition="external source found",
        priority=3,
    )

    ids = run(test_subtask, session_id, ledger)
    print(f"\n✅  Web Scout complete. {len(ids)} claims written.\n")