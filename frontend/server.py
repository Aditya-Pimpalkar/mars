"""
frontend/server.py
──────────────────
FastAPI server that serves the Evidence Heatmap and
provides a real-time claims API endpoint.

Run: uvicorn frontend.server:app --reload --port 8000
Then open: http://localhost:8000
"""
from __future__ import annotations

import sys
import uuid
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from es_client import get_client
from claim_ledger.ledger import ClaimLedger

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

# Track running pipeline sessions
pipeline_status = {}


# ── Sessions ───────────────────────────────────────────────

@app.get("/api/sessions")
def get_sessions():
    es = get_client()
    try:
        resp = es.search(
            index="claim-ledger-mars",
            body={
                "size": 0,
                "aggs": {
                    "sessions": {
                        "terms": {"field": "session_id", "size": 50}
                    }
                }
            }
        )
        buckets = resp["aggregations"]["sessions"]["buckets"]
        sessions = [b["key"] for b in sorted(
            buckets, key=lambda x: x["key"], reverse=True
        )]
        return {"sessions": sessions}
    except Exception as e:
        return {"sessions": [], "error": str(e)}


# ── Claims ─────────────────────────────────────────────────

@app.get("/api/claims")
def get_claims(session_id: str):
    es = get_client()
    ledger = ClaimLedger(es)
    try:
        claims  = ledger.get_claims(session_id)
        summary = ledger.session_summary(session_id)
        return {
            "claims":  [c.model_dump() for c in claims],
            "summary": summary,
        }
    except Exception as e:
        return {"claims": [], "summary": {}, "error": str(e)}


# ── Run pipeline ───────────────────────────────────────────

@app.post("/api/run")
def run_pipeline(body: dict):
    question    = body.get("question", "Why did API latency spike last Tuesday afternoon?")
    data_source = body.get("data_source", "demo")
    session_id  = f"mars_{uuid.uuid4().hex[:8]}"

    # Set initial status as plain string — consistent throughout
    pipeline_status[session_id] = "planning"

    def _run():
        try:
            from agents.planner   import run as planner_run
            from agents.verifier  import run as verifier_run
            from agents.retrieval import run as retrieval_run
            from agents.reviewer  import run as reviewer_run
            from agents.web_scout import run as web_scout_run
            from agents.planner   import Subtask

            es     = get_client()
            ledger = ClaimLedger(es)

            # Step 1 — Planner (calls Agent Builder)
            pipeline_status[session_id] = "planning"
            plan = planner_run(question, session_id=session_id, data_source=data_source)

# Step 2 — ES|QL Verifier
            pipeline_status[session_id] = "verifying"
            from agents.sources import get_source_config
            source_config = get_source_config(data_source)
            for s in plan.subtasks:
                if s.preferred_tool == "esql":
                    verifier_run(s, session_id, ledger, source_config=source_config)

            # Step 3 — Retrieval Agent
            pipeline_status[session_id] = "retrieving"
            for s in plan.subtasks:
                if s.preferred_tool in ("search_incidents", "search_runbooks"):
                    retrieval_run(s, session_id, ledger, source_config=source_config)

            # Step 4 — Web Scout
            pipeline_status[session_id] = "web_scouting"
            web_descriptions = {
                "demo":      "Find external corroboration for DB connection pool exhaustion and latency spikes",
                "weblogs":   "Find external articles about HTTP 404 503 errors web server anomalies nginx",
                "ecommerce": "Find external articles about ecommerce revenue trends sales patterns analytics",
            }
            web_subtask = Subtask(
                id="s_web",
                description=web_descriptions.get(data_source, web_descriptions["demo"]),
                preferred_tool="web",
                evidence_type="external_corroboration",
                stop_condition="external source found",
                priority=3,
            )
            existing = ledger.get_claims(session_id)
            web_scout_run(web_subtask, session_id, ledger, existing_claims=existing)

            # Step 5 — Reviewer
            pipeline_status[session_id] = "reviewing"
            reviewer_run(session_id, question=question, data_source=data_source)

            pipeline_status[session_id] = "complete"

        except Exception as e:
            pipeline_status[session_id] = f"error: {str(e)}"
            print(f"Pipeline error: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Return immediately — frontend starts polling right away
    return {"session_id": session_id, "status": "running"}


# ── Status ─────────────────────────────────────────────────

@app.get("/api/status")
def get_status(session_id: str):
    status = pipeline_status.get(session_id, "unknown")
    return {"session_id": session_id, "status": status}


# ── Data sources ───────────────────────────────────────────

@app.get("/api/sources")
def get_sources():
    from agents.sources import DATA_SOURCES
    return {"sources": [
        {"id": k, "label": v["label"], "description": v["description"]}
        for k, v in DATA_SOURCES.items()
    ]}

# ── Narrative ──────────────────────────────────────────────

@app.get("/api/narrative")
def get_narrative(session_id: str):
    es = get_client()
    try:
        resp = es.get(
            index="claim-ledger-mars",
            id=f"narrative_{session_id}",
        )
        return {"narrative": resp["_source"].get("narrative", "")}
    except Exception:
        return {"narrative": ""}


# ── Chart ──────────────────────────────────────────────────

@app.get("/api/chart")
def get_chart(session_id: str):
    es = get_client()
    try:
        from agents.verifier import _run_esql
        from agents.sources import DATA_SOURCES

        # Read data source from stored narrative
        data_source = "demo"
        try:
            nav = es.get(index="claim-ledger-mars", id=f"narrative_{session_id}")
            data_source = nav["_source"].get("data_source", "demo")
        except Exception:
            pass

        source_config = DATA_SOURCES.get(data_source, DATA_SOURCES["demo"])
        rows, _ = _run_esql(es, "spike_window", source_config=source_config)
        if not rows:
            return {"labels": [], "values": []}

        time_field  = list(rows[0].keys())[-1]
        value_field = list(rows[0].keys())[0]

        return {
            "labels": [str(r.get(time_field, ""))[:16].replace("T", " ") for r in rows],
            "values": [round(float(r.get(value_field, 0) or 0), 1) for r in rows],
        }
    except Exception as e:
        return {"labels": [], "values": [], "error": str(e)}

# ── Frontend ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "heatmap.html"
    return HTMLResponse(html_path.read_text())