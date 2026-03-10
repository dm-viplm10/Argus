# Agent Design Rationale

## Agent Abstractions (SOLID)

All graph nodes extend one of three base classes in `src/agent/base.py`:

- **BaseAgent** — Abstract base. Declares `@abstractmethod run(state) -> dict`.
- **StructuredOutputAgent** — For nodes using `ModelRouter.invoke()` with Pydantic schemas.
  Dependencies: `router`, `prompt_registry`. Used by Planner, Supervisor, Phase Strategist, Query Refiner, Risk Assessor, Synthesizer.
- **ReActAgent** — For ReAct loops (search + tools + structured submit).
  Dependencies: `registry`, `settings`, `prompt_registry`. Used by Search & Analyze, Verifier.
- **ToolNode** — For pure code (no LLM). Dependencies: `neo4j_conn`. Used by Graph Builder.

`@abstractmethod` is declared on `run()` in **every** class in the hierarchy — including the three intermediate classes. This is necessary because Python's ABC machinery only enforces `@abstractmethod` for methods that remain abstract in the immediate superclass. If an intermediate class overrides `run()` with a concrete body (e.g. `raise NotImplementedError`), Python considers the contract satisfied and allows subclasses to skip the implementation. Re-declaring `@abstractmethod` on each intermediate class restores enforcement at every level, so instantiating a node class that omits `run()` raises `TypeError` at import time rather than failing silently at runtime.

Prompts are centralized in `PromptRegistry` (`src/agent/prompts/registry.py`), mirroring the LLM registry pattern.

## Why Supervisor Pattern

The LangGraph Supervisor pattern was chosen because:

1. **Dynamic orchestration**: The supervisor acts as a research director, deciding
   which specialist to invoke next based on current findings. This maps naturally
   to how a real intelligence analyst directs investigation.

2. **Independent sub-agents**: Each agent class is testable in isolation with mocked
   `router`, `registry`, `prompt_registry`. Unit tests call `agent.run(state)`.

3. **Complex control flow**: The decision framework implements loops
   (re-search when findings are thin), conditional branching (skip risk assessment
   if no verified facts), phase progression, and optional Phase Strategist for
   dynamic phase expansion.

4. **State accumulation**: The `ResearchState` TypedDict with Annotated reducers
   means each node contributes to cumulative state without overwriting prior work.

## Why Custom StateGraph over create_supervisor

The `langgraph-supervisor` library is designed for simple agent-as-tool handoff patterns.
Our requirements exceed this:

- Custom state with 30+ research-specific fields
- Deterministic routing logic (not LLM-decided every time)
- Phase-based progression with budget tracking
- Specific instructions passed to each sub-agent per invocation

The LangChain team recommends custom supervisor logic for complex use cases.

## Model Selection Rationale

### GPT-4.1 (Supervisor, Phase Strategist)
Fast orchestration and routing. Phase strategy benefits from solid reasoning
without needing the heaviest models.

### Claude Sonnet 4.6 (Planner, Synthesizer)
Best structured reasoning for plan generation. Report synthesis needs the best
writing quality.

### GPT-4.1-mini (Query Refiner)
Fast and cheap for mechanical query refinement.

### Gemini 2.5 Flash (Search & Analyze, Verifier)
Efficient ReAct loop for both search + extraction and active fact verification.
Good balance of speed, tool use, and extraction quality across both nodes.

### Gemini 2.5 Pro (Risk Assessor)
Strong reasoning for identifying red flags and risk patterns from large volumes
of verified facts, entities, and relationships.

## Prompt Template System

Prompts live in `src/agent/prompts/templates/` as Markdown files — one per agent task:

```
templates/
├── supervisor.md
├── planner.md
├── phase_strategist.md
├── query_refiner.md
├── search_and_analyze.md
├── verifier.md
├── risk_assessor.md
└── synthesizer.md
```

`PromptRegistry` loads templates lazily on first use via `string.Template` (`$variable` syntax). Using `$variable` instead of `{variable}` means JSON examples inside prompts require no escaping.

**Startup validation** — `PromptRegistry().validate_all()` is called once inside the FastAPI lifespan context. It eagerly loads all eight required templates and raises immediately if any file is missing or malformed. This ensures prompt configuration errors surface at boot rather than mid-pipeline (after tokens have already been spent on earlier nodes).

```python
# src/main.py lifespan
PromptRegistry().validate_all()
logger.info("prompt_templates_validated")
```

## Checkpointing Strategy

Using `langgraph-checkpoint-redis` means:
- Each node completion writes a checkpoint
- Process restarts resume from last completed node
- Status queries read checkpoints directly
- "Time travel" debugging possible

State capture after a run uses a three-path priority chain:
1. `graph.aget_state()` — preferred (LangGraph managed snapshot)
2. `checkpointer.aget()` — direct checkpoint read as fallback
3. Root `on_chain_end` stream event — last resort when checkpointer is unavailable

## Research Job Timeout

Every research run is wrapped with `asyncio.wait_for(_execute_graph, timeout=RESEARCH_TIMEOUT_SECONDS)` inside `ResearchService._run_job()`. The default is 3 600 s (1 hour).

This prevents runaway LLM loops — e.g. a supervisor stuck in an infinite re-search cycle — from consuming unbounded API budget. On timeout:
- The `_execute_graph` coroutine is cancelled at the next `await` point
- Job status is set to `"failed"` with `"error": "timed out after Ns"`
- The failure is persisted to Redis
- The SSE sentinel (`None`) is still delivered, cleanly closing the stream

The timeout threshold is configurable via `RESEARCH_TIMEOUT_SECONDS` in `.env`.

## Streaming Architecture

`ResearchService._execute_graph()` streams raw LangGraph events using `graph.astream_events()`. For each event it pushes the raw dict into the job's `asyncio.Queue`. The SSE endpoint (`/research/{id}/stream`) pulls events from this queue and maps them to the client-facing format via `src/api/v1/sse_mapper`.

This design keeps SSE formatting logic out of the service layer and makes the event queue independently testable. `sse_mapper.to_sse_event()` is the single place that knows both the LangGraph internal event shape and the client-facing SSE protocol.

**SSE event types emitted by `sse_mapper`:**

| Event type | Trigger | Key data fields |
|---|---|---|
| `node_start` | `on_chain_start` for a graph node | `node` |
| `node_end` | `on_chain_end` for a graph node | `node`, optionally `extracted_facts`, `entities`, `verified_facts`, `risk_flags`, `pending_queries`, `phases`, `has_report`, `risk_score` |
| `token` | `on_chat_model_stream` (text content) | `node`, `content` |
| `thinking` | `on_chat_model_stream` (Claude thinking block) | `node`, `content` |
| `tool_start` | `on_tool_start` | `node`, `tool`, `input` (truncated to 500 chars) |
| `tool_end` | `on_tool_end` | `node`, `tool`, `output` (truncated to 500 chars) |

All other LangGraph event kinds are filtered out (return `None` from `to_sse_event`).

Individual nodes also emit fine-grained progress via `get_stream_writer()`:
```python
writer = get_stream_writer()
writer({"node": "search_and_analyze", "status": "started", "phase": 2})
writer({"node": "search_and_analyze", "status": "complete", "facts": 15})
```

These flow through `astream_events` as `on_custom_event` entries alongside the regular chain/chat/tool events.

## Web Scraping — Per-Domain Rate Limiting

`web_scrape.py` implements a per-domain asyncio lock with a politeness delay:

```python
# Atomically create the domain lock (no await between check and assign)
if domain not in _domain_locks:
    _domain_locks[domain] = asyncio.Lock()

async with _domain_locks[domain]:
    now = loop.time()
    wait_time = max(0.0, _POLITENESS_DELAY - (now - last))
    _domain_last_request[domain] = now + wait_time  # reserve the slot

if wait_time > 0:
    await asyncio.sleep(wait_time)       # sleep OUTSIDE the lock
```

Key properties:
- Lock creation is race-condition-safe: Python's asyncio cooperative scheduler only context-switches at `await` points, so the `if … not in … : = Lock()` block is effectively atomic.
- The politeness wait slot is reserved inside the lock; `asyncio.sleep` runs outside it so other coroutines waiting on the same domain are not stalled during the delay.
