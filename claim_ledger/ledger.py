"""
claim_ledger/ledger.py
──────────────────────
The Claim Ledger is the shared artifact all agents write to.
Every piece of evidence retrieved becomes a claim record here.
The Reviewer reads the full ledger to detect and resolve conflicts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

SourceType  = Literal["esql_data", "internal_doc", "web", "unknown"]
ClaimStatus = Literal["pending", "supported", "weakly_supported",
                      "contradicted", "unknown", "flagged"]
FollowUpStatus = Literal["idle", "querying", "resolved", "exhausted"]


class Claim(BaseModel):
    # Identity
    session_id:   str
    claim_id:     str = Field(default_factory=lambda: f"c_{uuid.uuid4().hex[:8]}")
    claim_text:   str

    # Provenance
    source_type:      SourceType  = "unknown"
    evidence_summary: str         = ""
    evidence_raw:     dict        = Field(default_factory=dict)
    source_timestamp: datetime | None = None

    # Confidence & status
    confidence:           float       = 0.5
    status:               ClaimStatus = "pending"
    conflicts_with:       list[str]   = Field(default_factory=list)
    resolution_reasoning: str         = ""

    # Follow-up tracking
    follow_up_count:   int            = 0
    follow_up_status:  FollowUpStatus = "idle"
    previous_queries:  list[str]      = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_es_doc(self) -> dict:
        d = self.model_dump()
        # Convert datetimes to ISO strings for ES
        for key in ("source_timestamp", "created_at", "updated_at"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


class ClaimLedger:
    """
    Wraps the claim-ledger-mars Elasticsearch index.
    All agents import this class to write claims.
    The Reviewer uses it to read and resolve conflicts.
    """

    INDEX = "claim-ledger-mars"

    def __init__(self, es_client):
        self.es = es_client

    # ── Write ──────────────────────────────────────────────────

    def write_claim(self, claim: Claim) -> str:
        """Index a claim. Returns the claim_id."""
        claim.updated_at = datetime.now(timezone.utc)
        self.es.index(
            index=self.INDEX,
            id=claim.claim_id,
            document=claim.to_es_doc(),
            refresh="wait_for",
        )
        return claim.claim_id

    def update_claim(self, claim_id: str, updates: dict) -> None:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.es.update(
            index=self.INDEX,
            id=claim_id,
            doc=updates,
            refresh="wait_for",
        )

    # ── Read ───────────────────────────────────────────────────

    def get_claims(self, session_id: str) -> list[Claim]:
        """Fetch all claims for a session — exclude narrative docs."""
        resp = self.es.search(
            index=self.INDEX,
            body={
                "query": {
                    "bool": {
                        "must": [{"term": {"session_id": session_id}}],
                        "must_not": [{"term": {"type": "agent_narrative"}}],
                    }
                },
                "size": 200,
                "sort": [{"created_at": "asc"}],
            },
        )
        return [Claim(**hit["_source"]) for hit in resp["hits"]["hits"]]

    def get_weak_claims(self, session_id: str,
                        threshold: float = 0.6) -> list[Claim]:
        """Claims that need follow-up: low confidence or unknown status."""
        resp = self.es.search(
            index=self.INDEX,
            body={
                "query": {
                    "bool": {
                        "must": [{"term": {"session_id": session_id}}],
                        "should": [
                            {"range":  {"confidence": {"lt": threshold}}},
                            {"term":   {"status": "unknown"}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "size": 50,
            },
        )
        return [Claim(**hit["_source"]) for hit in resp["hits"]["hits"]]

    def get_conflicts(self, session_id: str) -> list[Claim]:
        """Claims marked as contradicted."""
        resp = self.es.search(
            index=self.INDEX,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"session_id": session_id}},
                            {"term": {"status": "contradicted"}},
                        ]
                    }
                },
                "size": 50,
            },
        )
        return [Claim(**hit["_source"]) for hit in resp["hits"]["hits"]]

    # ── Conflict helpers ───────────────────────────────────────

    def mark_conflict(self, claim_id_a: str, claim_id_b: str) -> None:
        """Flag two claims as conflicting with each other."""
        self.update_claim(claim_id_a, {
            "status": "contradicted",
            "conflicts_with": [claim_id_b],
        })
        self.update_claim(claim_id_b, {
            "status": "contradicted",
            "conflicts_with": [claim_id_a],
        })

    def resolve_conflict(self, winning_id: str, losing_id: str,
                         reasoning: str) -> None:
        """Mark winner as supported, loser as contradicted with reasoning."""
        self.update_claim(winning_id, {
            "status": "supported",
            "resolution_reasoning": reasoning,
        })
        self.update_claim(losing_id, {
            "status": "contradicted",
            "resolution_reasoning": f"Overridden: {reasoning}",
        })

    # ── Summary ────────────────────────────────────────────────

    def session_summary(self, session_id: str) -> dict:
        claims = self.get_claims(session_id)
        counts = {}
        for c in claims:
            counts[c.status] = counts.get(c.status, 0) + 1

        follow_ups_fired = sum(c.follow_up_count for c in claims)

        # Exclude contradicted claims from confidence average
        # — they lost conflict resolution so shouldn't boost the score
        valid_claims = [c for c in claims if c.status != "contradicted"]
        avg_confidence = (
            sum(c.confidence for c in valid_claims) / len(valid_claims)
            if valid_claims else 0
        )

        return {
            "total_claims":      len(claims),
            "status_breakdown":  counts,
            "follow_ups_fired":  follow_ups_fired,
            "avg_confidence":    round(avg_confidence, 2),
            "conflicts_found":   counts.get("contradicted", 0),
        }
