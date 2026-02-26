# MARS — Multi-Agent Research Synthesizer

> Ask a complex operational question. Get a sourced, conflict-resolved answer in under 60 seconds.

Built for the [Elasticsearch Agent Builder Hackathon 2026](https://devpost.com/software/mars-multi-agent-research-synthesizer).

---

## What It Does

MARS is a multi-step AI agent system that investigates operational incidents by deploying five specialized agents in sequence. Each agent uses a different Elasticsearch tool to retrieve evidence, verify facts against raw data, detect contradictions between sources, and produce a structured root cause analysis with every claim traceable to its origin.

**Demo question:** *"Why did API latency spike last Tuesday afternoon?"*

MARS will:
1. Call Elastic Agent Builder to plan the investigation and fire custom ES|QL and Search tools
2. Run parameterized ES|QL queries against metrics and deployment data
3. Search internal incident tickets and runbooks for historical context
4. Search the web for external corroboration
5. Detect conflicts between sources (e.g. runbook says pool max = 50, ES|QL data shows 100)
6. Resolve conflicts using a trust hierarchy: ES|QL data > internal docs > web
7. Fire targeted follow-up queries for any weak evidence
8. Produce a sourced report with every claim linked to its evidence

---

## Key Features

| Feature | Description |
|---|---|
| **Elastic Agent Builder** | MARS agent in Kibana with 4 custom tools — `mars.spike_detector`, `mars.deploy_lookup`, `mars.doc_search`, `mars.runbook_search` |
| **ES\|QL Verifier** | 6 parameterized query templates against time-series metrics, logs, and deployments |
| **Hybrid Search** | BM25 search over incidents and runbooks for historical context |
| **Web Scout** | Tavily-powered external corroboration via web search |
| **Claim Ledger** | Every piece of evidence stored as a structured claim in Elasticsearch with confidence, status, and provenance |
| **Conflict Detection** | Automatic detection and resolution of contradictions between sources |
| **Auto Follow-Up Queries** | Weak claims (confidence < 0.68) trigger targeted re-investigation automatically |
| **Evidence Heatmap** | Real-time interactive matrix showing which source supported which claim |
| **Latency Chart** | p99 spike visualization from live ES\|QL data |
| **Agent Builder Narrative** | Full structured analysis from Claude Opus 4.5 via Elastic Agent Builder |

---

## Architecture

```
User Question
      │
      ▼
┌─────────────────────────────────────────────┐
│              Planner Agent                  │
│   Elastic Agent Builder · Claude Opus 4.5  │
│   Tools: spike_detector · deploy_lookup     │
│           doc_search · runbook_search       │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  ES|QL       │ │  Retrieval   │ │  Web Scout   │
│  Verifier    │ │  Agent       │ │  Agent       │
│              │ │              │ │              │
│ metrics-mars │ │incidents-mars│ │  Tavily API  │
│ logs-mars    │ │runbooks-mars │ │  Public web  │
│ deploy-mars  │ │              │ │              │
│ conf: 90-95% │ │ conf: 70-88% │ │ conf: 62-68% │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
              ┌─────────────────┐
              │  Claim Ledger   │
              │  (Elasticsearch)│
              └────────┬────────┘
                       ▼
          ┌────────────────────────┐
          │    Reviewer Agent      │
          │                        │
          │  · Conflict detection  │
          │  · Trust hierarchy     │
          │  · Auto follow-up      │
          │  · Final report        │
          └────────────┬───────────┘
                       ▼
          ┌────────────────────────┐
          │   Evidence Heatmap     │
          │   Latency Chart        │
          │   Agent Builder Report │
          └────────────────────────┘
```

### Trust Hierarchy

When two sources contradict each other, MARS resolves in favour of the higher-trust source:

```
① ES|QL data (ground truth)  >  ② Internal docs  >  ③ Web sources
```

### Elasticsearch Indices

| Index | Type | Contents |
|---|---|---|
| `metrics-mars` | Time-series | 133,917 docs — p99 latency, error rate, DB pool, RPS |
| `logs-mars` | Time-series | 201,669 docs — application logs with spike volume |
| `deployments-mars` | Event | 5 docs — deploy history including rollback |
| `incidents-mars` | Document | 3 docs — past incident tickets |
| `runbooks-mars` | Document | 3 docs — operational runbooks |
| `claim-ledger-mars` | Session | All agent evidence — grows with each pipeline run |

---

## Demo Scenarios

| Question | What MARS Demonstrates |
|---|---|
| *Why did API latency spike last Tuesday afternoon?* | All 3 agents active, 2 conflicts detected and resolved, ES\|QL wins both |
| *Is the January 21 incident a recurring problem?* | Pattern recognition, Jan 7 precedent surfaces strongly |
| *What caused the payment service timeout in January 2026?* | Follow-up query system — weak evidence triggers re-investigation |
| *Which deployment caused the latency regression?* | Deploy correlation, Agent Builder narrative with full timeline |
| *What is the rollback procedure for api-gateway?* | Runbook retrieval, procedural context from internal docs |

---

## Quick Start

**Prerequisites:** Python 3.12+, Elastic Cloud account ([cloud.elastic.co](https://cloud.elastic.co)), Tavily API key ([tavily.com](https://tavily.com))

```bash
git clone https://github.com/YOUR_USERNAME/mars
cd mars

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env — add your Elastic Cloud endpoint, API keys, and Tavily key

# Create indices and generate demo data (~5 min)
python indices/setup.py
python ingest/generate.py

# Verify the pipeline
python agents/reviewer.py
# Expected: 15 claims, 2 conflicts detected, 8 follow-ups fired

# Start the frontend
pip install fastapi uvicorn
uvicorn frontend.server:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000), type a question, and click **Run MARS**.

---

## Environment Variables

| Variable | Description |
|---|---|
| `ES_HOST` | Elasticsearch endpoint URL |
| `ES_API_KEY` | Elastic Cloud API key |
| `ELASTIC_KIBANA_HOST` | Kibana endpoint URL (the `.kb.` subdomain) |
| `ELASTIC_AGENT_ID` | Agent Builder agent ID (default: `mars-research-synthesizer`) |
| `ELASTIC_AGENT_API_KEY` | Kibana API key for Agent Builder API |
| `TAVILY_API_KEY` | Tavily web search API key |
| `ANTHROPIC_API_KEY` | Fallback LLM key if Agent Builder is unreachable |
| `FOLLOWUP_CONFIDENCE_THRESHOLD` | Confidence below which follow-ups trigger (default: `0.68`) |

---

## Elastic Agent Builder Setup

MARS requires a custom agent configured in Kibana with four tools. In Kibana → Agents → Tools, create:

**`mars.spike_detector`** (ES|QL) — Detects p99 latency anomalies
```
FROM metrics-mars
| WHERE @timestamp >= "2026-01-21T13:30:00Z" AND @timestamp <= "2026-01-21T16:00:00Z"
| STATS max_p99 = MAX(latency_p99) BY bucket = DATE_TRUNC(1 minute, @timestamp)
| WHERE max_p99 > 200
| SORT bucket ASC
| LIMIT 50
```

**`mars.deploy_lookup`** (ES|QL) — Finds correlated deployments
```
FROM deployments-mars
| WHERE @timestamp >= "2026-01-21T13:30:00Z" AND @timestamp <= "2026-01-21T16:00:00Z"
| KEEP @timestamp, version, service, author, changes
| SORT @timestamp ASC
| LIMIT 100
```

**`mars.doc_search`** — Index search over `incidents-mars`

**`mars.runbook_search`** — Index search over `runbooks-mars`

Create a new agent with ID `mars-research-synthesizer`, assign all four tools, and use the system prompt from `agents/planner.py`.

---

## Project Structure

```
mars/
├── agents/
│   ├── planner.py       # Calls Agent Builder, routes subtasks by question type
│   ├── verifier.py      # ES|QL queries against time-series data
│   ├── retrieval.py     # Hybrid search over incidents and runbooks
│   ├── web_scout.py     # Tavily web search for external corroboration
│   └── reviewer.py      # Conflict detection, resolution, follow-up, report
├── claim_ledger/
│   └── ledger.py        # Shared Elasticsearch artifact — all agents write here
├── indices/
│   ├── mappings.json    # All 6 index schemas
│   └── setup.py         # Creates all indices
├── ingest/
│   └── generate.py      # Generates 335k+ synthetic documents
├── frontend/
│   ├── server.py        # FastAPI server with 8 endpoints
│   └── heatmap.html     # Evidence Heatmap — real-time UI
├── es_client.py         # Shared Elasticsearch client (Cloud + local)
├── requirements.txt
├── .env.example
└── LICENSE              # Apache 2.0
```

---

## Hackathon Tracks

- **Multi-agent** — Five agents plan, retrieve, verify, and reconcile independently
- **Tool-driven** — Each agent selects the right Elasticsearch tool for its subtask
- **Measurable impact** — Manual investigation: 30-60 min → MARS: ~45 seconds
- **Time-series aware** — ES|QL queries over metrics and logs at 1-minute resolution
- **Reliable action** — Every decision explained with sourced, traceable evidence

---

## Tech Stack

- **Elasticsearch Cloud Serverless** — ES|QL · Hybrid Search · 335k+ documents
- **Elastic Agent Builder** — 4 custom tools · Claude Opus 4.5 reasoning model
- **Python 3.12 + FastAPI** — Agent orchestration · Pipeline API server
- **Tavily** — Web search for external corroboration
- **Chart.js + marked.js** — Evidence Heatmap · Latency chart · Narrative rendering

---

## License

Apache 2.0 — see [LICENSE](LICENSE)