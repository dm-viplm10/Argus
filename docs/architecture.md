# Architecture Overview

## System Components

```
┌─────────────┐    ┌──────────────────────────────────────────────┐
│   Client     │───▶│   FastAPI  (port 8000)                       │
│  (REST/SSE)  │    │                                              │
└─────────────┘    │  CORSMiddleware (ALLOWED_ORIGINS from config) │
                   │  Request-ID middleware                         │
                   └──────────────┬───────────────────────────────┘
                                  │ Depends(get_research_service)
                   ┌──────────────▼───────────────────────────────┐
                   │   ResearchService                             │
                   │                                              │
                   │  • create_job() — init job, start task        │
                   │  • _run_job()   — asyncio.wait_for wrapper    │
                   │  • _execute_graph() — full pipeline + state   │
                   │  • cancel_job(), get_status(), SSE queues     │
                   │  • Timeout: RESEARCH_TIMEOUT_SECONDS (3600s)  │
                   └──────────────┬───────────────────────────────┘
                                  │ compile_research_graph()
    ┌─────────────────────────────▼────────────────────────────────────────┐
    │              LangGraph Supervisor (Redis-checkpointed StateGraph)      │
    │                                                                       │
    │  Supervisor ──▶ Planner ──▶ Phase Strategist (optional)              │
    │      │               │              │                                 │
    │      │               └──▶ Query Refiner ◀──┘                         │
    │      │                          │                                    │
    │      │              Search & Analyze (ReAct: Tavily + scrape + extract)│
    │      │                          │                                    │
    │      │              Verifier (ReAct: search + submit_verification)    │
    │      │                          │                                    │
    │      │              Risk Assessor ◀──┘                               │
    │      │                          │                                    │
    │      │              Graph Builder (Neo4j, no LLM) ──▶ Synthesizer    │
    │      │                                    │                          │
    │      ◀────────────────────────────────────┘                          │
    └──────────────────────────────────────────────────────────────────────┘
                          │                    │
                   ┌──────▼──────┐    ┌───────▼────────┐
                   │    Neo4j    │    │     Redis       │
                   │  (graph DB) │    │  (job status,   │
                   │  port 7474  │    │   checkpoints,  │
                   │  ACID txns  │    │   eval state)   │
                   └─────────────┘    └────────────────┘
```

## Service Layer

### ResearchService (`src/services/research_service.py`)

Owns all job lifecycle state. API endpoints delegate to it; they hold no job state themselves.

```
create_job(request)
  └─ asyncio.create_task(_run_job)
       └─ asyncio.wait_for(_execute_graph, timeout=RESEARCH_TIMEOUT_SECONDS)
            ├─ compile_research_graph(...)
            ├─ graph.astream_events(...)  ← pushes raw events to per-job asyncio.Queue
            ├─ three-path state capture (aget_state → direct checkpoint → stream event)
            └─ Redis persistence (eval state key + job key)
```

- **Job state** lives in `_jobs` (in-process dict) and Redis (`argus:job:{id}`, 7-day TTL).
- **Eval state** is persisted separately to `argus:evalstate:{id}` (30-day TTL) so evaluations can be run long after the run completes.
- **SSE** consumers attach to the per-job `asyncio.Queue`; the sentinel `None` signals stream end.
- **Timeout**: if `_execute_graph` does not complete within `RESEARCH_TIMEOUT_SECONDS`, it is cancelled and the job is marked `failed` with `"error": "timed out after Ns"`.
- **Cancellation**: `cancel_job()` both sets a cooperative-cancellation flag (checked by the supervisor between steps) and hard-cancels the asyncio task.

## Agent Architecture

All graph nodes implement the `BaseAgent` abstraction (`src/agent/base.py`):

- **StructuredOutputAgent** — Planner, Supervisor, Phase Strategist, Query Refiner, Risk Assessor, Synthesizer. Use `ModelRouter.invoke()` with Pydantic structured output. Prompts from `PromptRegistry`.
- **ReActAgent** — Search & Analyze, Verifier. Use `create_react_agent()` with Tavily, web_scrape, and a submit tool. Prompts from `PromptRegistry`.
- **ToolNode** — Graph Builder. Pure code, no LLM. Writes entities/relationships to Neo4j.

`@abstractmethod` is declared on `run()` at every level of the hierarchy (including the intermediate classes). Python's ABC machinery therefore enforces that every concrete node class implements `run()` — instantiating a class that omits it raises `TypeError` at import time.

The graph wires agents via dependency injection: each agent receives `router`, `registry`, `settings`, `neo4j_conn`, or `prompt_registry` as needed. Node callables are `agent.run`.

## Data Flow

1. Client POSTs to `/api/v1/research` with target info
2. `ResearchService.create_job()` initialises job state in memory + Redis, starts inline asyncio task
3. `_run_job` wraps `_execute_graph` in `asyncio.wait_for` (hard timeout)
4. `_execute_graph` compiles and streams the LangGraph pipeline:
   - Planner creates phased research strategy
   - Phase Strategist (when dynamic) evaluates Phase 1 and adds deeper phases
   - Query Refiner generates targeted search queries
   - Search & Analyze executes Tavily + web scrape, extracts facts/entities in one ReAct pass
   - Verifier actively verifies claims via web search (ReAct)
   - Risk Assessor identifies red flags
   - Graph Builder writes to Neo4j (ACID write transaction, no LLM)
   - Synthesizer generates final report
5. Each node checkpoints state to Redis
6. On completion, final state is persisted to `argus:evalstate:{id}` (30-day TTL)
7. Client polls status or streams via SSE from the per-job event queue; `src/api/v1/sse_mapper.to_sse_event()` translates raw LangGraph events into typed SSE events (`node_start`, `node_end`, `token`, `thinking`, `tool_start`, `tool_end`, `done`)

## Multi-Model Routing

All LLM calls go through OpenRouter. The ModelRouter handles:
- Task-to-model mapping (each node uses a specific model)
- Automatic fallback chains on failure
- Token usage tracking per model
- LangSmith tracing of all invocations

**Per-node model assignment (`src/models/llm_registry.py`):**

| Node | OpenRouter model slug | Temperature |
|------|----------------------|-------------|
| Supervisor | `openai/gpt-4.1` | 0.1 |
| Planner | `anthropic/claude-sonnet-4.6` | 0.3 |
| Phase Strategist | `openai/gpt-4.1` | 0.3 |
| Query Refiner | `openai/gpt-4.1-mini` | 0.4 |
| Search & Analyze | `google/gemini-2.5-flash` | 0.1 |
| Verifier | `google/gemini-2.5-flash` | 0.5 |
| Risk Assessor | `google/gemini-2.5-pro` | 0.5 |
| Synthesizer | `anthropic/claude-sonnet-4.6` | 0.2 |
| Evaluator (LLM judge) | `openai/gpt-4.1` | 0.6 |
| Graph Builder | — (no LLM) | — |

Fallback order (from `llm_registry.FALLBACK_CHAINS`):
```
Claude Sonnet 4.6 → GPT-4.1 → Gemini 2.5 Pro
GPT-4.1           → Claude Sonnet 4.6 → Gemini 2.5 Pro
GPT-4.1-mini      → Gemini 2.5 Flash → Claude Sonnet 4.6
Gemini 2.5 Pro    → Claude Sonnet 4.6 → GPT-4.1
Gemini 2.5 Flash  → GPT-4.1-mini → Claude Sonnet 4.6
```

## Registries

### LLMRegistry (`src/models/llm_registry.py`)
Task → model mapping, fallback chains, usage tracking.

### PromptRegistry (`src/agent/prompts/registry.py`)
Task → prompt template. Templates are Markdown files (`src/agent/prompts/templates/<task>.md`) using `string.Template` (`$variable` substitution) so JSON braces inside prompts need no escaping.

```python
registry = PromptRegistry()
prompt = registry.get_prompt("supervisor", target_name=..., facts=...)
```

`validate_all()` is called once at application startup (inside the lifespan context) to eagerly load every required template. A missing or malformed file raises immediately — before any research run is attempted — preventing silent LLM budget waste. Required tasks: `supervisor`, `planner`, `phase_strategist`, `query_refiner`, `search_and_analyze`, `verifier`, `risk_assessor`, `synthesizer`.

## Security & Reliability

### CORS
`CORSMiddleware` uses an explicit origin allow-list (`settings.ALLOWED_ORIGINS`) instead of `"*"`.
Allowed methods: `GET`, `POST`, `DELETE`. Allowed headers: `Content-Type`, `X-Request-ID`.
`allow_credentials=True` is valid because origins are explicit (browsers reject `"*"` + credentials).

Configure for production:
```
ALLOWED_ORIGINS=["https://app.example.com"]
```

### Neo4j Transactions
`Neo4jConnection.execute_write()` uses `session.execute_write(_work)` — a proper write transaction with ACID guarantees and automatic retry on transient errors (e.g. leader election, network blip). `execute_read()` similarly uses `session.execute_read(_work)`.

Auto-commit `session.run()` is not used for any write operations.

### Research Job Timeout
Every research run is bounded by `asyncio.wait_for(_execute_graph, timeout=RESEARCH_TIMEOUT_SECONDS)` (default 3 600 s = 1 hour). On expiry the pipeline coroutine is cancelled and the job is marked `failed`. Configure via `RESEARCH_TIMEOUT_SECONDS` in `.env`.

### Web Scraping — Per-Domain Rate Limiting
`web_scrape.py` holds a `dict[str, asyncio.Lock]` keyed by domain. Each domain lock is created atomically (no `await` between existence check and assignment, so the asyncio cooperative scheduler cannot interleave). The politeness wait slot is reserved inside the lock and released before `asyncio.sleep()` so other coroutines waiting on the same domain are not blocked during the sleep.

### Evaluation Results Cap
The in-process `_evaluations` store is an `OrderedDict` capped at 1 000 entries. When the cap is exceeded the oldest entry is evicted (`popitem(last=False)`), preventing unbounded memory growth across long-running deployments.

### Settings Caching
`get_settings()` is decorated with `@lru_cache(maxsize=1)`. The `.env` file is read and validated exactly once per process — not on every FastAPI dependency injection call.
