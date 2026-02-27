# MARS — Multi-Agent Research Synthesizer

> Ask a complex operational question. Get a sourced, conflict-resolved answer in under 3 minutes.

**Built for the [Elasticsearch Agent Builder Hackathon 2026](https://devpost.com/software/mars-multi-agent-research-synthesizer)**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-green.svg)](https://python.org)
[![Elasticsearch](https://img.shields.io/badge/Elasticsearch-Cloud%20Serverless-orange.svg)](https://cloud.elastic.co)

---

## What It Does

MARS is a multi-step AI agent system that investigates operational incidents by deploying five specialized agents in sequence. Each agent uses a different Elasticsearch tool to retrieve evidence, verify facts against raw data, detect contradictions between sources, and produce a structured root cause analysis — with every claim traceable to its origin.

**Demo question:** *"Why did API latency spike last Tuesday afternoon?"*

MARS will:
1. Call **Elastic Agent Builder** to plan the investigation and fire custom ES|QL and Search tools
2. Run parameterized **ES|QL queries** against metrics and deployment data
3. Search internal incident tickets and runbooks for **historical context**
4. Search the web for **external corroboration**
5. **Detect conflicts** between sources (e.g. runbook says pool max = 50, ES|QL shows 100)
6. **Resolve conflicts** using a trust hierarchy: ES|QL data > internal docs > web
7. Fire **targeted follow-up queries** for any weak evidence
8. Produce a **sourced report** with every claim linked to its evidence

**Result:** What takes an engineer 30-60 minutes manually, MARS does in ~3 minutes.

---

## Live Demo

🔗 **[https://web-production-d6146.up.railway.app](https://web-production-d6146.up.railway.app)**

> Pipeline takes ~2-3 minutes. The heatmap builds in real time as agents run.
> Use the pre-filled question for each data source.

---

## Key Features

| Feature | Description |
|---|---|
| **Elastic Agent Builder** | 3 agents in Kibana with 7 custom ES|QL + Search tools |
| **ES\|QL Verifier** | 8 parameterized query templates against time-series metrics, logs, and deployments |
| **Hybrid Search** | BM25 search over incidents and runbooks for historical context |
| **Web Scout** | Tavily-powered external corroboration via web search |
| **Claim Ledger** | Every piece of evidence stored as a structured claim in Elasticsearch with confidence, status, and provenance |
| **Conflict Detection** | Automatic detection and resolution of contradictions between sources |
| **Auto Follow-Up Queries** | Weak claims (confidence < 0.68) trigger targeted re-investigation automatically |
| **Evidence Heatmap** | Real-time interactive matrix showing which source supported which claim |
| **Multi-Source Toggle** | Switch between Demo Data, Sample Web Logs, and Sample eCommerce live |
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
┌──────────────────────────────────────────────┐
│              Evidence Heatmap UI             │
│                                              │
│  ⚡ Agent Builder Analysis (answer first)   │
│  📈 Latency / Traffic / Revenue Chart       │
│  🔥 Evidence Heatmap (supporting claims)    │
└──────────────────────────────────────────────┘
```

### Trust Hierarchy

When two sources contradict each other, MARS resolves in favour of the higher-trust source:

```
① ES|QL data (ground truth)  >  ② Internal docs  >  ③ Web sources
```

---

## Data Sources

MARS supports three data sources switchable live from the UI:

| Source | Index | Agent | Best Question |
|---|---|---|---|
| **Demo Data** | `metrics-mars`, `logs-mars`, `deployments-mars` | `mars-research-synthesizer` | *Why did API latency spike last Tuesday?* |
| **Sample Web Logs** | `kibana_sample_data_logs` (14k real requests) | `mars-weblogs-analyzer` | *Are there any HTTP errors in the web logs?* |
| **Sample eCommerce** | `kibana_sample_data_ecommerce` (4.6k orders) | `mars-ecommerce-analyzer` | *What is the revenue trend this month?* |

---

## Elasticsearch Indices

| Index | Type | Contents |
|---|---|---|
| `metrics-mars` | Time-series | 133,917 docs — p99 latency, error rate, DB pool, RPS by region |
| `logs-mars` | Time-series | 201,669 docs — application logs with 10x spike volume during incident |
| `deployments-mars` | Event | 5 docs — deploy history including v2.4.1 villain deploy + rollback |
| `incidents-mars` | Document | 3 docs — past incident tickets (INC-2041 has planted wrong timestamp) |
| `runbooks-mars` | Document | 3 docs — operational runbooks (RB-0034 has planted wrong pool max) |
| `claim-ledger-mars` | Session | All agent evidence — grows with each pipeline run |

> **Planted contradictions:** INC-2041 states spike started at 14:45 (ES|QL shows 14:25). RB-0034 states pool max = 50 (ES|QL shows 100). MARS detects and resolves both automatically.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [Elastic Cloud account](https://cloud.elastic.co) (free trial available)
- [Tavily API key](https://tavily.com) (free tier: 1000 searches/month)
- Anthropic or OpenAI API key (fallback if Agent Builder unavailable)

### 1 — Clone and install

```bash
git clone https://github.com/Aditya-Pimpalkar/mars.git
cd mars

python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install fastapi uvicorn
```

### 2 — Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Elasticsearch Cloud
ES_HOST=https://your-deployment.es.us-east1.gcp.elastic.cloud
ES_API_KEY=your_api_key_here

# Kibana + Agent Builder
ELASTIC_KIBANA_HOST=https://your-deployment.kb.us-east1.gcp.elastic.cloud
ELASTIC_AGENT_ID=mars-research-synthesizer
ELASTIC_AGENT_API_KEY=your_kibana_api_key_here

# Web Scout
TAVILY_API_KEY=your_tavily_api_key_here

# Fallback LLM (used if Agent Builder unreachable)
ANTHROPIC_API_KEY=your_anthropic_key_here

# Pipeline tuning (optional)
FOLLOWUP_CONFIDENCE_THRESHOLD=0.68
FOLLOWUP_MAX_ITERATIONS=3
```

### 3 — Create indices and generate demo data

```bash
# Create all 6 Elasticsearch indices
python indices/setup.py

# Generate 335k+ synthetic documents with planted contradictions
# Takes ~5 minutes on Elastic Cloud
python ingest/generate.py
```

### 4 — Set up Elastic Agent Builder in Kibana

Go to **Kibana → Agents → Tools** and create these tools:

---

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

**`mars.weblogs_errors`** (ES|QL) — Traffic and error rates from web logs
```
FROM kibana_sample_data_logs
| WHERE @timestamp >= now() - 7 days
| STATS
    total_requests = COUNT(*),
    avg_bytes = AVG(bytes),
    max_bytes = MAX(bytes)
    BY bucket = DATE_TRUNC(6 hours, @timestamp)
| SORT bucket ASC
| LIMIT 50
```

**`mars.weblogs_top_errors`** (ES|QL) — Top 404/503 URLs
```
FROM kibana_sample_data_logs
| WHERE @timestamp >= now() - 7 days
    AND (response == "404" OR response == "503")
| STATS error_count = COUNT(*)
    BY request, response, host
| SORT error_count DESC
| LIMIT 20
```

**`mars.ecommerce_trends`** (ES|QL) — Daily revenue and order volumes
```
FROM kibana_sample_data_ecommerce
| WHERE order_date >= now() - 30 days
| STATS
    total_orders = COUNT(*),
    total_revenue = SUM(taxful_total_price),
    avg_order = AVG(taxful_total_price),
    max_order = MAX(taxful_total_price)
    BY bucket = DATE_TRUNC(1 day, order_date)
| SORT bucket ASC
| LIMIT 50
```

**`mars.ecommerce_categories`** (ES|QL) — Sales by day of week
```
FROM kibana_sample_data_ecommerce
| WHERE order_date >= now() - 30 days
| STATS
    total_orders = COUNT(*),
    total_revenue = SUM(taxful_total_price)
    BY day_of_week
| SORT total_revenue DESC
| LIMIT 10
```

---

Then create three **Agents** in Kibana → Agents:

| Agent ID | Tools | Data Source |
|---|---|---|
| `mars-research-synthesizer` | `mars.spike_detector`, `mars.deploy_lookup`, `mars.doc_search`, `mars.runbook_search` | Demo Data |
| `mars-weblogs-analyzer` | `mars.weblogs_errors`, `mars.weblogs_top_errors` | Sample Web Logs |
| `mars-ecommerce-analyzer` | `mars.ecommerce_trends`, `mars.ecommerce_categories` | Sample eCommerce |

Use the system prompt from `agents/planner.py` → `SYSTEM_PROMPT` for all three agents.

> **Sample data:** Install via Kibana → Home → Add sample data → **Sample web logs** + **Sample eCommerce**

### 5 — Verify the pipeline works

```bash
python agents/reviewer.py
```

Expected output:
```
✅ Connected to Elasticsearch
15 claims loaded
2 contradictions detected and resolved
8/9 follow-ups resolved
Session ID: mars_xxxxxxxx
```

### 6 — Start the UI

```bash
uvicorn frontend.server:app --reload --port 8000
```

Open **http://localhost:8000**, select a data source, type a question, and click **▶ Run MARS**.

---

## Demo Scenarios

### Demo Data (synthetic incident — Jan 21, 2026)

| Question | What MARS Demonstrates |
|---|---|
| *Why did API latency spike last Tuesday afternoon?* | All 3 agents, 2 conflicts detected + resolved, ES\|QL wins both |
| *Is the January 21 incident a recurring problem?* | Pattern recognition, Jan 7 precedent INC-1987 surfaces |
| *What caused the payment service timeout in January 2026?* | Follow-up query system — weak evidence triggers re-investigation |
| *Which deployment caused the latency regression?* | Deploy correlation, v2.4.1 by carol identified |
| *What is the rollback procedure for api-gateway?* | Runbook retrieval, RB-0041 emergency procedure |

### Sample Web Logs (real Kibana data)

| Question | What MARS Demonstrates |
|---|---|
| *Are there any HTTP errors or anomalies in the web logs?* | ES\|QL over real nginx logs, 404/503 error detection |
| *Why are there so many 404 errors on the website?* | Top error URL analysis, broken artifact paths |
| *Is the server returning 503 errors and when did they spike?* | Time-series error pattern from real data |

### Sample eCommerce (real Kibana data)

| Question | What MARS Demonstrates |
|---|---|
| *What is the revenue trend and which categories are performing best?* | Daily revenue ES\|QL, $350k+ across 4.6k orders |
| *Which day of the week has the highest sales volume?* | Thursday/Friday peak detection |
| *Are there any unusual patterns in order values this month?* | Anomaly detection — $2,250 bulk order identified |

---

## How It Works — The Pipeline

```
1. PLANNING (~20s)
   User question → Agent Builder API → Claude Opus 4.5
   Fires mars.spike_detector, mars.deploy_lookup, mars.doc_search, mars.runbook_search
   Returns: narrative + subtask routing decisions

2. VERIFICATION (~10s)
   ES|QL Verifier runs parameterized queries against time-series data
   Writes claims with 90-95% confidence to Claim Ledger
   Source type: esql_data (highest trust)

3. RETRIEVAL (~5s)
   Retrieval Agent runs BM25 search over incidents + runbooks
   Writes claims with 70-88% confidence to Claim Ledger
   Source type: internal_doc (medium trust)

4. WEB SCOUTING (~15s)
   Web Scout fires 2 Tavily searches based on ES|QL findings
   Writes claims with 62-68% confidence to Claim Ledger
   Source type: web (lowest trust)

5. REVIEW (~10s)
   Reviewer reads all claims from Claim Ledger
   Detects contradictions between sources
   Resolves: esql_data > internal_doc > web
   Fires follow-up ES|QL queries for weak claims (conf < 0.68)
   Generates final sourced report

Total: ~2-3 minutes end-to-end
```

---

## Project Structure

```
mars/
├── agents/
│   ├── planner.py       # Calls Agent Builder, routes subtasks by question + data source
│   ├── verifier.py      # ES|QL queries, handles demo/weblogs/ecommerce templates
│   ├── retrieval.py     # BM25 hybrid search over incidents and runbooks
│   ├── web_scout.py     # Tavily web search for external corroboration
│   ├── reviewer.py      # Conflict detection, trust hierarchy, auto follow-up
│   └── sources.py       # Data source profiles (demo/weblogs/ecommerce)
├── claim_ledger/
│   └── ledger.py        # Shared Elasticsearch artifact — all agents write here
├── indices/
│   ├── mappings.json    # All 6 index schemas
│   └── setup.py         # Creates all indices on Elastic Cloud
├── ingest/
│   └── generate.py      # Generates 335k+ synthetic docs with planted contradictions
├── frontend/
│   ├── server.py        # FastAPI server — 8 API endpoints
│   └── heatmap.html     # Evidence Heatmap UI — real-time, Chart.js, marked.js
├── architecture.html    # Interactive architecture diagram
├── es_client.py         # Elasticsearch client (Cloud API key + local basic auth)
├── requirements.txt     # Python dependencies
├── .env.example         # All required environment variables with descriptions
├── Procfile             # Railway deployment
├── runtime.txt          # Python 3.12 for Railway
└── LICENSE              # Apache 2.0
```

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the Evidence Heatmap UI |
| `/api/run` | POST | Starts pipeline in background, returns `session_id` immediately |
| `/api/status?session_id=X` | GET | Returns current pipeline stage for live status indicator |
| `/api/claims?session_id=X` | GET | Returns all claims — polled every 3s by frontend |
| `/api/sessions` | GET | Returns all past session IDs |
| `/api/narrative?session_id=X` | GET | Returns Agent Builder narrative markdown |
| `/api/chart?session_id=X` | GET | Returns chart data from ES\|QL (labels + values) |
| `/api/sources` | GET | Returns available data source configs |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ES_HOST` | ✅ | Elasticsearch endpoint URL |
| `ES_API_KEY` | ✅ | Elastic Cloud API key |
| `ELASTIC_KIBANA_HOST` | ✅ | Kibana endpoint URL (`.kb.` subdomain) |
| `ELASTIC_AGENT_ID` | ✅ | Agent Builder agent ID (default: `mars-research-synthesizer`) |
| `ELASTIC_AGENT_API_KEY` | ✅ | Kibana API key for Agent Builder |
| `TAVILY_API_KEY` | ✅ | Tavily web search API key |
| `ANTHROPIC_API_KEY` | Optional | Fallback LLM if Agent Builder unreachable |
| `OPENAI_API_KEY` | Optional | Alternative fallback LLM |
| `LLM_PROVIDER` | Optional | `anthropic` or `openai` (default: `anthropic`) |
| `FOLLOWUP_CONFIDENCE_THRESHOLD` | Optional | Confidence below which follow-ups trigger (default: `0.68`) |
| `FOLLOWUP_MAX_ITERATIONS` | Optional | Max follow-up attempts per claim (default: `3`) |

---

## Running Without Elastic Cloud

For local development using Docker:

```bash
# Start local Elasticsearch + Kibana
docker compose up -d

# Wait ~30 seconds for services to start
python es_client.py   # Should print: Connected to Elasticsearch 8.x

# Then follow Quick Start steps 3-6 as normal
```

The codebase auto-detects Cloud vs local via the presence of `ES_API_KEY` in your `.env`.

> **Note:** Elastic Agent Builder requires Kibana and is only available on Elastic Cloud. For local development, MARS falls back to calling Claude or OpenAI directly for planning.

---

## Hackathon Tracks

- ✅ **Multi-agent** — Five agents plan, retrieve, verify, and reconcile independently
- ✅ **Elastic Agent Builder** — 3 agents, 7 custom tools, Claude Opus 4.5 reasoning
- ✅ **ES|QL tool** — `mars.spike_detector` + `mars.deploy_lookup` + 6 parameterized templates
- ✅ **Search tool** — `mars.doc_search` + `mars.runbook_search` in Kibana
- ✅ **Measurable impact** — 30-60 min manual → ~3 min automated
- ✅ **Time-series aware** — ES|QL queries at 1-minute resolution over 30 days of data
- ✅ **Reliable action** — Every decision sourced, every conflict explained

---

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| **Search & Data** | Elasticsearch Cloud Serverless | All 6 indices, ES\|QL, hybrid search, Claim Ledger |
| **Agent Framework** | Elastic Agent Builder (Kibana) | 3 agents with 7 custom tools |
| **LLM** | Claude Opus 4.5 | Planner reasoning via Elastic's Anthropic connector |
| **Web Search** | Tavily API | Web Scout external corroboration |
| **Backend** | Python 3.12 + FastAPI | Agent logic, pipeline orchestration, API server |
| **Frontend** | HTML/CSS/JS | Evidence Heatmap, Chart.js, marked.js narrative |
| **Data Generation** | Python Faker | 335k+ synthetic documents with planted contradictions |
| **Infrastructure** | Elastic Cloud Serverless (GCP us-east1) + Railway | Fully managed, no cluster sizing required |

---

## License

Apache 2.0 — see [LICENSE](LICENSE)

---

*Built for the Elasticsearch Agent Builder Hackathon 2026*