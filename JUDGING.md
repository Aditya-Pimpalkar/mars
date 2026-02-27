# JUDGING.md — MARS Evaluation Guide

> This guide is written specifically for hackathon judges.
> It covers the fastest path to running MARS, what to look for, and where each requirement is satisfied.

---

## TL;DR — What MARS Does

MARS takes a natural language question like *"Why did API latency spike last Tuesday?"* and runs five specialized AI agents in sequence — each using a different Elasticsearch tool — to produce a fully sourced, conflict-resolved root cause analysis in ~3 minutes.

**The key differentiator:** When sources contradict each other (and they do — we planted two deliberate contradictions in the demo data), MARS automatically detects and resolves them using a trust hierarchy: ES|QL data always wins over documentation.

---

## Hackathon Requirements — Where They Are Satisfied

| Requirement | Satisfied By                                           | Where to See It |
|---|--------------------------------------------------------|---|
| **Elastic Agent Builder** | `mars-research-synthesizer` agent in Kibana            | Agent Builder narrative panel in UI, `agents/planner.py` `_call_agent_builder()` |
| **ES\|QL tool** | `mars.spike_detector` + `mars.deploy_lookup` in Kibana | Green cells in ES\|QL Data column of heatmap |
| **Search tool** | `mars.doc_search` + `mars.runbook_search` in Kibana    | Green/amber cells in Internal Docs column |
| **Multi-step reasoning** | 5-agent pipeline with shared Claim Ledger              | Terminal output during pipeline run |
| **Multi-agent** | Planner → Verifier → Retrieval → Web Scout → Reviewer  | `agents/` directory, pipeline status indicator in UI |
| **Measurable impact** | 30-60 min manual → ~45 sec auto                        | **Measurable impact** | 30-60 min manual → ~3 min automated | Stats bar in UI |
| **Open source + Apache 2.0** | `LICENSE` file                                         | Root of repository |

---

## Fastest Path to Running MARS

### Option A — Full Setup (~30 minutes)

**Prerequisites:** Elastic Cloud account, Tavily API key, Python 3.12

```bash
git clone https://github.com/Aditya-Pimpalkar/mars.git
cd mars
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install fastapi uvicorn
cp .env.example .env
# Edit .env with your Elastic Cloud credentials
python indices/setup.py
python ingest/generate.py      # ~5 min — generates 335k docs
uvicorn frontend.server:app --reload --port 8000
```

Open http://localhost:8000

### Option B — Smoke Test Only (~5 minutes)

To verify the pipeline without the UI:

```bash
python agents/reviewer.py
```

Expected output:
```
✅  Connected to Elasticsearch
15 claims loaded
2 contradictions detected and resolved
8/9 follow-ups resolved
Session ID: mars_xxxxxxxx
Open: http://localhost:8000?session=mars_xxxxxxxx
```

---

## What To Look For In The UI

### Step 1 — Run the demo question

Select **Demo Data** source and click **▶ Run MARS** with the pre-filled question:
> *"Why did API latency spike last Tuesday afternoon?"*

Watch the pipeline status bar cycle through:
```
🧠 Agent Builder planning subtasks...
⚡ ES|QL Verifier querying metrics...
📖 Retrieval Agent searching docs...
🌐 Web Scout searching external sources...
🔍 Reviewer detecting contradictions...
✅ Pipeline complete
```

Claims appear row by row in the heatmap as each agent writes them.

### Step 2 — Verify Agent Builder is working

Scroll to the **⚡ Agent Builder Analysis** panel (appears first, above the heatmap).

This is the direct response from `mars-research-synthesizer` in Kibana — Claude Opus 4.5 reasoning over your ES|QL and Search tool results. You should see:
- Root Cause section identifying `idle_timeout` misconfiguration
- Timeline table: 14:20 deploy → 14:25 spike → 14:32 peak → 15:02 rollback
- Evidence Sources listing all 4 tools that fired

### Step 3 — Find the conflict detection

In the heatmap, look for rows with **red pulsing cells** and the `⚡ contradicted` badge.

There are **2 planted contradictions** in the demo data:

| Contradiction | What ES|QL Says | What the Doc Says | Winner |
|---|---|---|---|
| Spike start time | 14:25 UTC | INC-2041 says 14:45 UTC | ES|QL |
| DB pool maximum | 100 connections | RB-0034 says 50 connections | ES|QL |

Hover over the red cell to see the full contradiction reasoning in the tooltip.

### Step 4 — Check the latency chart

Below the Agent Builder Analysis, the **p99 Latency chart** shows the spike visualized from live ES|QL data — starting at 256ms at 14:25, peaking at 890ms at 14:32, recovering by 15:02.

The red dot marks the exact peak.

### Step 5 — Switch data sources

Click **Sample Web Logs** — the question input changes to:
> *"Are there any HTTP errors or anomalies in the web logs?"*

Click **▶ Run MARS**. This runs against `kibana_sample_data_logs` (14k real requests) using a separate `mars-weblogs-analyzer` Agent Builder agent. The chart changes to show web traffic patterns.

Click **Sample eCommerce** — runs against `kibana_sample_data_ecommerce` (4.6k real orders) using `mars-ecommerce-analyzer`. The chart shows daily revenue trends.

---

## Code Walkthrough — Key Files

### The 5 Agents

| File | What It Does | Elastic Tool Used |
|---|---|---|
| `agents/planner.py` | Calls Agent Builder API, routes subtasks based on question + data source | Elastic Agent Builder |
| `agents/verifier.py` | Runs ES\|QL templates, writes 90-95% confidence claims | ES\|QL |
| `agents/retrieval.py` | BM25 search over incidents + runbooks, writes 70-88% confidence claims | Elasticsearch Search |
| `agents/web_scout.py` | Tavily web search, writes 62-68% confidence claims | External (Tavily) |
| `agents/reviewer.py` | Detects conflicts, resolves trust hierarchy, fires follow-ups | Pure Python logic |

### The Shared Artifact

`claim_ledger/ledger.py` — The Claim Ledger is an Elasticsearch index (`claim-ledger-mars`) that all agents write to and the Reviewer reads from. This is the architectural centrepiece — agents are decoupled and communicate only through this ledger.

### The Trust Hierarchy (line ~180 in `agents/reviewer.py`)

```python
TRUST_ORDER = {"esql_data": 3, "internal_doc": 2, "web": 1}
# Higher number wins conflict resolution
```

### The Conflict Detection (line ~200 in `agents/reviewer.py`)

Two rules detect the planted contradictions:
1. If two claims mention "spike" or "latency" but contain different timestamps → conflict
2. If two claims mention "pool" but contain different numeric values → conflict

---

## Architecture Diagram

See `architecture.html` in the repo root — open in any browser for a clean professional diagram.

---

## Data Volume

```
metrics-mars      133,917 documents   1-minute resolution, 30 days
logs-mars         201,669 documents   application logs with spike volume
deployments-mars        5 documents   including v2.4.1 villain deploy
incidents-mars          3 documents   INC-2041 has planted wrong timestamp
runbooks-mars           3 documents   RB-0034 has planted wrong pool max
claim-ledger-mars   grows per run     ~15 claims per session
─────────────────────────────────────
Total:            335,594 documents
```

---

## Common Issues

**Pipeline fails at planning stage**
- Check `ELASTIC_KIBANA_HOST` and `ELASTIC_AGENT_API_KEY` in `.env`
- Verify `mars-research-synthesizer` agent exists in Kibana → Agents
- MARS will fall back to Claude/OpenAI if Agent Builder is unreachable

**No claims appearing in heatmap**
- Check `ES_HOST` and `ES_API_KEY` in `.env`
- Run `python es_client.py` to verify connection

**Sample data sources show no ES|QL claims**
- Install Kibana sample data: Kibana → Home → Add sample data
- Create `mars-weblogs-analyzer` and `mars-ecommerce-analyzer` agents in Kibana

**`ingest/generate.py` times out**
- Elastic Cloud Serverless can be slow on first ingest
- Re-run — it's idempotent and will skip existing documents

---

## What Makes This Different

Most hackathon submissions call an LLM and display the response. MARS does something harder:

1. **Multi-source evidence collection** — three independent agents retrieve evidence from different source types simultaneously
2. **Adversarial data** — the demo data deliberately contains wrong information to test conflict detection
3. **Traceable claims** — every statement in the final report is linked to the exact ES|QL row or document that produced it
4. **Self-correcting** — weak claims automatically trigger follow-up queries without human intervention
5. **Real data demo** — switches between synthetic incident data and real Kibana sample datasets live

The Claim Ledger design — where every agent writes structured evidence records to Elasticsearch rather than passing strings between functions — is the architectural decision that makes all of this possible.

---

*MARS — Multi-Agent Research Synthesizer*
*Elasticsearch Agent Builder Hackathon 2026*
*github.com/Aditya-Pimpalkar/mars*