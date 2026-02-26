"""
agents/planner.py
─────────────────
PHASE 1 ✅  ← Build this first

The Planner Agent takes a research question and decomposes it
into 3–7 subtasks, each with a preferred tool, expected evidence
type, and stop condition.

Output is strict JSON — validated by Pydantic before passing
downstream to retrieval agents.
"""
from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel
from dotenv import load_dotenv
from datetime import datetime, timezone
from es_client import get_client

load_dotenv()

Tool = Literal["esql", "search_incidents", "search_runbooks", "web"]

AGENT_IDS = {
    "demo":      "mars-research-synthesizer",
    "weblogs":   "mars-weblogs-analyzer",
    "ecommerce": "mars-ecommerce-analyzer",
}

class Subtask(BaseModel):
    id:             str
    description:    str
    preferred_tool: Tool
    evidence_type:  str          # e.g. "timestamp", "numeric_metric", "procedure"
    stop_condition: str          # e.g. "spike window identified with 1-min precision"
    priority:       int = 1      # 1 = high, 2 = medium, 3 = low


class Plan(BaseModel):
    session_id:     str
    question:       str
    subtasks:       list[Subtask]
    rationale:      str          # Planner's reasoning summary


SYSTEM_PROMPT = """You are the Planner agent for MARS, a Multi-Agent Research Synthesizer.

Given a research question, decompose it into 3–7 subtasks that together fully answer it.

Rules:
- Prefer ES|QL (tool: "esql") for ANY time-bound, numeric, or metric-based subtasks
- Use "search_incidents" for historical precedents and similar past events  
- Use "search_runbooks" for procedures, known fixes, and operational steps
- Use "web" ONLY if the question requires external corroboration (rare)
- Each subtask must be atomic — answerable by a single query
- Order subtasks by dependency (things that must be found first go first)

Return ONLY valid JSON matching this schema — no preamble, no markdown:
{
  "subtasks": [
    {
      "id": "s1",
      "description": "...",
      "preferred_tool": "esql" | "search_incidents" | "search_runbooks" | "web",
      "evidence_type": "...",
      "stop_condition": "...",
      "priority": 1
    }
  ],
  "rationale": "brief explanation of decomposition strategy"
}"""

def _call_agent_builder(question: str, agent_id: str = None) -> str:
    """Call MARS agent via Elastic Agent Builder API."""
    import requests
    import os

    kibana_host = os.getenv("ELASTIC_KIBANA_HOST")
    if agent_id is None:
        agent_id = os.getenv("ELASTIC_AGENT_ID", "mars-research-synthesizer")
    api_key     = os.getenv("ELASTIC_AGENT_API_KEY")

    resp = requests.post(
        f"{kibana_host}/api/agent_builder/converse",
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type":  "application/json",
            "kbn-xsrf":      "true",
        },
        json={
            "input":    question,
            "agent_id": agent_id,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract the narrative response + all tool steps
    response_text = data.get("response", {}).get("message", "")
    steps = data.get("steps", [])

    # Extract tool results for the Reviewer to use
    tool_evidence = []
    for step in steps:
        if step.get("type") == "tool_call":
            tool_evidence.append({
                "tool":    step.get("tool_id"),
                "results": step.get("results", []),
            })

    return json.dumps({
        "narrative": response_text,
        "tool_evidence": tool_evidence,
    })

def run(question: str, session_id: str, data_source: str = "demo") -> Plan:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    # Route to correct Agent Builder agent based on data source
    kibana_host = os.getenv("ELASTIC_KIBANA_HOST", "")
    if kibana_host:
        agent_id = AGENT_IDS.get(data_source, "mars-research-synthesizer")
        raw = _call_agent_builder(question, agent_id=agent_id)
    elif provider == "anthropic":
        raw = _call_anthropic(question)
    else:
        raw = _call_openai(question)

    # Strip markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    if not raw:
        raise ValueError("LLM returned empty response")

    parsed = json.loads(raw)

# Handle Agent Builder response format
    if "narrative" in parsed:
        question_lower = question.lower()

        subtasks = []

# Route subtasks based on data source
        if data_source == "weblogs":
            subtasks.append(Subtask(
                id="s1",
                description="Analyze HTTP error rates and traffic volume from web logs",
                preferred_tool="esql",
                evidence_type="weblogs",
                stop_condition="error patterns found",
                priority=1,
            ))
            subtasks.append(Subtask(
                id="s2",
                description=f"{question} web server errors 404 503 incidents past history",
                preferred_tool="search_incidents",
                evidence_type="historical_precedent",
                stop_condition="similar incident found",
                priority=2,
            ))
            subtasks.append(Subtask(
                id="s3",
                description=f"{question} web server error diagnosis remediation steps",
                preferred_tool="search_runbooks",
                evidence_type="procedure",
                stop_condition="remediation steps found",
                priority=2,
            ))

        elif data_source == "ecommerce":
            subtasks.append(Subtask(
                id="s1",
                description="Analyze daily revenue trends and order volumes",
                preferred_tool="esql",
                evidence_type="ecommerce",
                stop_condition="revenue trend found",
                priority=1,
            ))
            subtasks.append(Subtask(
                id="s2",
                description=f"{question} ecommerce sales revenue incidents past history",
                preferred_tool="search_incidents",
                evidence_type="historical_precedent",
                stop_condition="similar pattern found",
                priority=2,
            ))
            subtasks.append(Subtask(
                id="s3",
                description=f"{question} ecommerce sales analysis investigation steps",
                preferred_tool="search_runbooks",
                evidence_type="procedure",
                stop_condition="analysis steps found",
                priority=2,
            ))

        else:
            # Demo data — keyword-based routing
            if any(k in question_lower for k in
                   ["latency", "spike", "slow", "performance", "timeout",
                    "down", "outage", "degraded", "response time"]):
                subtasks.append(Subtask(
                    id="s1",
                    description="Identify latency spike window and magnitude from metrics",
                    preferred_tool="esql",
                    evidence_type="timestamp",
                    stop_condition="spike window identified",
                    priority=1,
                ))
                subtasks.append(Subtask(
                    id="s2",
                    description="Find deployments in the incident window",
                    preferred_tool="esql",
                    evidence_type="deploy",
                    stop_condition="deploy found",
                    priority=1,
                ))

            subtasks.append(Subtask(
                id=f"s{len(subtasks)+1}",
                description=f"{question} past incidents precedent history",
                preferred_tool="search_incidents",
                evidence_type="historical_precedent",
                stop_condition="similar incident found",
                priority=2,
            ))
            subtasks.append(Subtask(
                id=f"s{len(subtasks)+1}",
                description=f"{question} diagnosis remediation procedure steps",
                preferred_tool="search_runbooks",
                evidence_type="procedure",
                stop_condition="remediation steps found",
                priority=2,
            ))

        # Store narrative
        es = get_client()
        es.index(
            index="claim-ledger-mars",
            id=f"narrative_{session_id}",
            document={
                "session_id": session_id,
                "type":       "agent_narrative",
                "narrative":  parsed["narrative"],
                "question":   question,
                "data_source": data_source,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            refresh="wait_for",
        )

        return Plan(
            session_id=session_id,
            question=question,
            subtasks=subtasks,
            rationale=parsed["narrative"][:200],
        )

def _call_anthropic(question: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model  = os.getenv("ANTHROPIC_REASONING_MODEL", "claude-opus-4-6")

    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return msg.content[0].text


def _call_openai(question: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model  = os.getenv("OPENAI_REASONING_MODEL", "gpt-4o")

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": question},
        ],
    )
    return resp.choices[0].message.content


if __name__ == "__main__":
    # Quick smoke test
    import uuid
    session = str(uuid.uuid4())[:8]
    question = "Why did API latency spike last Tuesday afternoon?"
    print(f"\nQuestion: {question}\n")
    plan = run(question, session_id=session)
    print(f"Plan for session {plan.session_id}:")
    for s in plan.subtasks:
        print(f"  [{s.preferred_tool.upper():18s}] {s.id}: {s.description}")
    print(f"\nRationale: {plan.rationale}")
