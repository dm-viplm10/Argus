# Argus

Autonomous AI OSINT (Open Source Intelligence) investigation agent. Conducts multi-layered web research, extracts and cross-references facts, identifies risk patterns, maps entity relationships, and generates identity graphs.

*Named after Argus Panoptes — the all-seeing giant of Greek mythology.*

## Architecture

```
                    ┌────────────────────────────┐
                    │     FastAPI (port 8000)     │
                    │  REST + SSE Streaming API   │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────▼───────────────┐
                    │     ResearchService         │
                    │  (job lifecycle, asyncio    │
                    │   tasks, event queues,      │
                    │   1-hour hard timeout)      │
                    └────────────┬───────────────┘
                                 │
          ┌──────────────────────▼──────────────────────┐
          │           LangGraph Supervisor               │
          │            (GPT-4.1 routing)                 │
          │  Routes to sub-agents based on state         │
          └──┬───┬───┬───┬───┬───┬───┬───┬─────────────┘
             │   │   │   │   │   │   │   │
             ▼   ▼   ▼   ▼   ▼   ▼   ▼   ▼
          Plan Phase Query Search Verify Risk Graph Synth
          ner  Strat Refnr &Analyze er    Assr Bldr  esizer
```

### Model Strategy

| Node | Model | Purpose |
|------|-------|---------|
| Supervisor | GPT-4.1 | Orchestration & routing |
| Planner | Claude Sonnet 4.6 | Research plan generation |
| Phase Strategist | GPT-4.1 | Dynamic phase strategy post–Phase 1 |
| Query Refiner | GPT-4.1-mini | Search query generation |
| Search & Analyze | Gemini 2.5 Flash | Web research + fact/entity extraction (ReAct) |
| Verifier | Gemini 2.5 Flash | Active fact verification via search (ReAct) |
| Risk Assessor | Gemini 2.5 Pro | Red flag identification |
| Graph Builder | — | Neo4j writes (no LLM) |
| Synthesizer | Claude Sonnet 4.6 | Report generation |

All models accessed via **OpenRouter** with automatic fallback chains.

### Tech Stack

- **AI Orchestration**: LangGraph v1.0 (Supervisor + ReAct patterns)
- **LLM Gateway**: OpenRouter (multi-model)
- **Search**: Tavily API (AI-native search)
- **Graph Database**: Neo4j 5 Community (ACID write transactions)
- **Web Framework**: FastAPI (async)
- **Execution**: Inline asyncio tasks with hard timeout (`RESEARCH_TIMEOUT_SECONDS`)
- **Checkpointing**: langgraph-checkpoint-redis (durable execution)
- **Observability**: LangSmith + Structlog
- **Streaming**: SSE via `astream_events` → `ResearchService` event queue

## Prerequisites

- Docker 24.0+ & Docker Compose v2
- Python 3.12+
- API keys: OpenRouter, Tavily, LangSmith (optional)

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API keys and Neo4j password.
# For local Docker dev, use NEO4J_PASSWORD=research_agent_dev to match docker-compose.

# 2. First-time setup
make setup

# 3. Start all services
make up

# 4. Verify infrastructure
make verify

# 5. Open API docs
open http://localhost:8000/docs

# 6. Open Neo4j browser
open http://localhost:7474
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/research` | Start a new investigation |
| DELETE | `/api/v1/research/{id}/cancel` | Cancel a running job |
| GET | `/api/v1/research/{id}` | Get full results |
| GET | `/api/v1/research/{id}/status` | Real-time status |
| GET | `/api/v1/research/{id}/stream` | SSE progress stream |
| GET | `/api/v1/graph/{id}` | Identity graph (JSON, D3-compatible) |
| GET | `/api/v1/graph/{id}/export?format=...` | Export graph (`json`, `graphml`, `png`, `jpeg`) |
| POST | `/api/v1/evaluate` | Run evaluation for a completed research job |
| GET | `/api/v1/evaluate/{evaluation_id}/results` | Get evaluation results by ID |
| GET | `/api/v1/health` | Liveness probe — returns `{"status": "healthy"}` |
| GET | `/api/v1/ready` | Readiness probe — checks Neo4j **and** Redis connectivity |

### Start a Research Job

```bash
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "target_name": "Timothy Overturf",
    "target_context": "CEO of Sisu Capital",
    "objectives": ["biographical", "financial", "risk_assessment", "connections"],
    "max_depth": 5
  }'
```

### Evaluation

Completed research runs can be evaluated against ground truth (e.g. `timothy_overturf.json` in `src/evaluation/ground_truth/`). The API compares pipeline state (facts, entities, relationships, risk flags) to the ground truth and computes metrics (fact precision, network fidelity, risk detection rate, depth score, efficiency, source quality). With **LLM judge** enabled (default), GPT-4.1 scores each metric and produces a full markdown report.

- **API:** `POST /api/v1/evaluate` with `research_id`, optional `ground_truth_file` and `use_llm_judge`; `GET /api/v1/evaluate/{evaluation_id}/results` to fetch a stored result. See [docs/api.md](docs/api.md) for request/response details.
- **UI:** Use the **Evaluate** tab: enter a completed research ID, optional ground truth filename, run evaluation; metadata is shown as JSON and the evaluation report as markdown.
- **CLI:** `make evaluate` runs the evaluation script in the container.

## Development

```bash
make up          # Start services
make down        # Stop services
make logs        # Tail logs
make test        # Run tests
make lint        # Run linter
make format      # Auto-format
make evaluate    # Run evaluation script (ground truth comparison; optional LLM judge via API)
make graph-export # Export identity graph
```

## Project Structure

```
src/
├── main.py              # FastAPI app factory + lifespan (startup validation, CORS)
├── config.py            # Pydantic Settings (lru_cache-d, read once at startup)
├── api/                 # REST API endpoints
│   ├── dependencies.py  # DI singletons: neo4j, redis, registry, research_service
│   ├── graph_image.py   # Matplotlib graph rendering (Agg backend, singleton guard)
│   └── v1/
│       ├── research.py  # Research CRUD — delegates to ResearchService
│       ├── graph.py     # Graph fetch/export (shared _fetch_graph_data helper)
│       ├── evaluations.py # Evaluation store (capped at 1,000 entries, LRU eviction)
│       ├── health.py    # /health (liveness) + /ready (Neo4j + Redis readiness)
│       ├── sse_mapper.py  # Maps raw LangGraph astream_events to SSE (event_type, data) pairs
│       └── schemas/     # Pydantic request/response models (research, graph, evaluation)
├── agent/               # LangGraph supervisor + nodes
│   ├── base.py          # BaseAgent, StructuredOutputAgent, ReActAgent, ToolNode
│   │                    # (all three intermediate classes enforce @abstractmethod)
│   ├── graph.py         # StateGraph definition, agent wiring
│   ├── edges.py         # Conditional routing
│   ├── state.py         # ResearchState TypedDict
│   ├── nodes/           # 9 agent classes (planner, supervisor, phase_strategist, etc.)
│   ├── prompts/
│   │   ├── registry.py  # PromptRegistry — loads .md templates, validate_all() at startup
│   │   └── templates/   # One .md file per task (supervisor, planner, verifier, …)
│   └── tools/           # Tavily search, web_scrape (per-domain asyncio lock)
├── models/              # LLM registry, model router, schemas
├── services/
│   ├── research_service.py  # Job lifecycle: create, stream, cancel, timeout
│   ├── checkpoint_service.py
│   └── cache_service.py
├── graph_db/            # Neo4j connection (ACID read/write transactions), schema, queries
├── evaluation/          # Metrics, ground truth files, evaluator, LLM judge
└── utils/               # Logging, rate limiting, retry
```

## Observability

- **LangSmith**: Full execution traces at https://smith.langchain.com
- **Neo4j Browser**: Identity graph at http://localhost:7474
- **RedisInsight**: Cache/queue state at http://localhost:8001
- **Structlog**: JSON-formatted structured logs

## Configuration Highlights

Key settings in `.env` (see [docs/deployment.md](docs/deployment.md) for the full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `RESEARCH_TIMEOUT_SECONDS` | `3600` | Hard wall-clock cap per research run |
| `ALLOWED_ORIGINS` | `["http://localhost:8501","http://localhost:3000"]` | CORS allow-list |
| `LOG_FORMAT` | `json` | `json` for production, `console` for local dev |
