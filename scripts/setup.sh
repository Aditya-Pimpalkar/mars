#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MARS — Full Setup Script
# Run once to go from zero to a working demo environment.
#
# Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh
#
# What it does:
#   1. Copies .env.example → .env  (if not already present)
#   2. Starts Elasticsearch + Kibana via Docker Compose
#   3. Waits for Elasticsearch to be healthy
#   4. Sets the kibana_system password (required by Kibana)
#   5. Installs Python dependencies
#   6. Creates all 6 Elasticsearch indices
#   7. Generates and ingests synthetic demo data
#   8. Smoke-tests the Planner Agent
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[MARS]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step()    { echo -e "\n${GREEN}━━━ $1 ━━━${NC}"; }

# ── 1. Environment ─────────────────────────────────────────────
step "Environment setup"

if [ ! -f .env ]; then
    cp .env.example .env
    warn ".env created from .env.example — add your LLM API keys before running agents"
else
    info ".env already exists — skipping copy"
fi

# ── 2. Docker Compose ──────────────────────────────────────────
step "Starting Elasticsearch + Kibana"

if ! command -v docker &> /dev/null; then
    error "Docker not found. Install Docker Desktop: https://docs.docker.com/get-docker/"
fi

docker compose up -d
info "Containers started"

# ── 3. Wait for Elasticsearch ──────────────────────────────────
step "Waiting for Elasticsearch to be healthy"

ELASTIC_PASSWORD="${ELASTIC_PASSWORD:-mars_hackathon}"
MAX_WAIT=120
WAITED=0

until curl -s -u "elastic:${ELASTIC_PASSWORD}" \
    "http://localhost:9200/_cluster/health" \
    | grep -q '"status":"green\|yellow"'; do
    if [ $WAITED -ge $MAX_WAIT ]; then
        error "Elasticsearch did not become healthy within ${MAX_WAIT}s"
    fi
    echo -n "."
    sleep 3
    WAITED=$((WAITED + 3))
done

echo ""
info "Elasticsearch is healthy ✅"

# ── 4. Set Kibana system password ──────────────────────────────
step "Configuring Kibana system user"

KIBANA_PASSWORD="${KIBANA_PASSWORD:-mars_kibana}"
curl -s -X POST \
    -u "elastic:${ELASTIC_PASSWORD}" \
    "http://localhost:9200/_security/user/kibana_system/_password" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"${KIBANA_PASSWORD}\"}" > /dev/null

info "kibana_system password set"

# ── 5. Python dependencies ─────────────────────────────────────
step "Installing Python dependencies"

if ! command -v python3 &> /dev/null; then
    error "Python 3 not found. Install Python 3.11+: https://python.org"
fi

python3 -m pip install -q -r requirements.txt
info "Python dependencies installed"

# ── 6. Create indices ──────────────────────────────────────────
step "Creating Elasticsearch indices"

python3 indices/setup.py

# ── 7. Generate synthetic data ─────────────────────────────────
step "Generating synthetic demo data"

python3 ingest/generate.py

# ── 8. Smoke test ──────────────────────────────────────────────
step "Smoke test — Planner Agent"

if grep -q "^ANTHROPIC_API_KEY=sk-ant" .env || grep -q "^OPENAI_API_KEY=sk-" .env; then
    python3 agents/planner.py
else
    warn "No LLM API key found in .env — skipping Planner smoke test"
    warn "Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and run: python agents/planner.py"
fi

# ── Done ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  MARS setup complete! 🚀${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Elasticsearch:  http://localhost:9200"
echo "  Kibana:         http://localhost:5601  (user: elastic / mars_hackathon)"
echo ""
echo "  Next steps:"
echo "  1. Add your LLM API keys to .env"
echo "  2. Run:  python agents/planner.py"
echo "  3. Phase 2 builds retrieval.py and verifier.py"
echo ""
