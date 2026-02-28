# Architecture Overview

## System Components

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│   Client     │───▶│   FastAPI    │───▶│   Celery     │
│  (REST/SSE)  │    │  (port 8000) │    │   Worker     │
└─────────────┘    └──────────────┘    └──────┬───────┘
                                              │
                   ┌──────────────────────────▼───────────────────────────┐
                   │              LangGraph Supervisor                     │
                   │         (Redis-checkpointed StateGraph)              │
                   │                                                       │
                   │  Supervisor ──▶ Planner ──▶ Query Refiner            │
                   │      │                          │                     │
                   │      │         Search & Scrape ◀┘                    │
                   │      │              │                                 │
                   │      │         Analyzer ──▶ Verifier                  │
                   │      │                          │                     │
                   │      │         Risk Assessor ◀──┘                    │
                   │      │              │                                 │
                   │      │         Graph Builder ──▶ Synthesizer          │
                   │      │                               │                │
                   │      ◀──────────────────────────────┘                │
                   └──────────────────────────────────────────────────────┘
                          │                    │
                   ┌──────▼──────┐    ┌───────▼────────┐
                   │    Neo4j    │    │     Redis       │
                   │  (graph DB) │    │  (cache/broker/ │
                   │  port 7474  │    │  checkpoints)   │
                   └─────────────┘    └────────────────┘
```

## Data Flow

1. Client POSTs to `/api/v1/research` with target info
2. FastAPI creates job, dispatches to Celery worker
3. Celery worker initializes LangGraph supervisor graph
4. Supervisor routes through sub-agents based on state:
   - Planner creates 5-phase research strategy
   - Query Refiner generates targeted search queries
   - Search agent executes Tavily searches + web scraping
   - Analyzer extracts facts, entities, relationships
   - Verifier cross-references and assigns confidence scores
   - Risk Assessor identifies red flags (via Grok 3)
   - Graph Builder writes to Neo4j
   - Synthesizer generates final report
5. Each node checkpoints state to Redis
6. Client polls status or streams via SSE

## Multi-Model Routing

All LLM calls go through OpenRouter. The ModelRouter handles:
- Task-to-model mapping (each node uses a specific model)
- Automatic fallback chains on failure
- Token usage tracking per model
- LangSmith tracing of all invocations

Fallback order:
```
Claude Sonnet 4.6 → Gemini 2.5 Pro → GPT-4.1-mini
Gemini 2.5 Pro    → Claude Sonnet 4.6 → GPT-4.1-mini
Grok 3            → Claude Sonnet 4.6 → GPT-4.1-mini
GPT-4.1-mini      → Claude Sonnet 4.6
```
