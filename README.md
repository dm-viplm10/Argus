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
                    │   Inline asyncio tasks      │
                    │  (Redis checkpoints)        │
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
| Verifier | Gemini 2.5 Pro | Active fact verification via search (ReAct) |
| Risk Assessor | GPT-4.1 | Red flag identification |
| Graph Builder | — | Neo4j writes (no LLM) |
| Synthesizer | Claude Sonnet 4.6 | Report generation |

All models accessed via **OpenRouter** with automatic fallback chains.

### Tech Stack

- **AI Orchestration**: LangGraph v1.0 (Supervisor + ReAct patterns)
- **LLM Gateway**: OpenRouter (multi-model)
- **Search**: Tavily API (AI-native search)
- **Graph Database**: Neo4j 5 Community
- **Web Framework**: FastAPI (async)
- **Execution**: Inline asyncio tasks (SSE streaming)
- **Checkpointing**: langgraph-checkpoint-redis (durable execution)
- **Observability**: LangSmith + Structlog
- **Streaming**: SSE via `get_stream_writer()`

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
| GET | `/api/v1/research/{id}` | Get full results |
| GET | `/api/v1/research/{id}/status` | Real-time status |
| GET | `/api/v1/research/{id}/stream` | SSE progress stream |
| GET | `/api/v1/graph/{id}` | Identity graph (JSON) |
| GET | `/api/v1/graph/{id}/export?format=graphml` | Export graph |
| POST | `/api/v1/evaluate` | Run evaluation |
| GET | `/api/v1/health` | Health check |

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

## Development

```bash
make up          # Start services
make down        # Stop services
make logs        # Tail logs
make test        # Run tests
make lint        # Run linter
make format      # Auto-format
make evaluate    # Run evaluation framework
make graph-export # Export identity graph
```

## Project Structure

```
src/
├── main.py              # FastAPI app factory
├── config.py            # Pydantic Settings
├── api/                 # REST API endpoints
├── agent/               # LangGraph supervisor + nodes
│   ├── base.py          # BaseAgent, StructuredOutputAgent, ReActAgent, ToolNode
│   ├── graph.py         # StateGraph definition, agent wiring
│   ├── edges.py         # Conditional routing
│   ├── state.py         # ResearchState TypedDict
│   ├── nodes/           # 9 agent classes (planner, supervisor, phase_strategist, etc.)
│   ├── prompts/         # Prompt templates + PromptRegistry
│   └── tools/           # Tavily search, web scrape
├── models/              # LLM registry, model router, schemas
├── services/            # Business logic services
├── graph_db/            # Neo4j connection, schema, queries
├── evaluation/          # Metrics, ground truth, evaluator
└── utils/               # Logging, rate limiting, retry
```

## Observability

- **LangSmith**: Full execution traces at https://smith.langchain.com
- **Neo4j Browser**: Identity graph at http://localhost:7474
- **RedisInsight**: Cache/queue state at http://localhost:8001
- **Structlog**: JSON-formatted structured logs
