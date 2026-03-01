# Architecture Overview

## System Components

```
┌─────────────┐    ┌──────────────┐
│   Client     │───▶│   FastAPI    │
│  (REST/SSE)  │    │  (port 8000) │
└─────────────┘    └──────┬───────┘
                         │ inline asyncio tasks
                         │ (SSE streaming)
    ┌────────────────────▼─────────────────────────────────────────────────┐
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
                   │  (graph DB) │    │  (cache/job     │
                   │  port 7474  │    │  checkpoints)   │
                   └─────────────┘    └────────────────┘
```

## Agent Architecture

All graph nodes implement the `BaseAgent` abstraction:

- **StructuredOutputAgent** — Planner, Supervisor, Phase Strategist, Query Refiner, Risk Assessor, Synthesizer. Use `ModelRouter.invoke()` with Pydantic structured output. Prompts from `PromptRegistry`.
- **ReActAgent** — Search & Analyze, Verifier. Use `create_react_agent()` with Tavily, web_scrape, and a submit tool. Prompts from `PromptRegistry`.
- **ToolNode** — Graph Builder. Pure code, no LLM. Writes entities/relationships to Neo4j.

The graph wires agents via dependency injection: each agent receives `router`, `registry`, `settings`, `neo4j_conn`, or `prompt_registry` as needed. Node callables are `agent.run`.

## Data Flow

1. Client POSTs to `/api/v1/research` with target info
2. FastAPI creates job, starts inline asyncio task (enables SSE streaming)
3. Task initializes LangGraph supervisor graph with checkpointing
4. Supervisor routes through sub-agents based on state:
   - Planner creates phased research strategy
   - Phase Strategist (when dynamic) evaluates Phase 1 and adds deeper phases
   - Query Refiner generates targeted search queries
   - Search & Analyze executes Tavily + web scrape, extracts facts/entities in one ReAct pass
   - Verifier actively verifies claims via web search (ReAct)
   - Risk Assessor identifies red flags
   - Graph Builder writes to Neo4j (no LLM)
   - Synthesizer generates final report
5. Each node checkpoints state to Redis
6. Client polls status or streams via SSE

## Multi-Model Routing

All LLM calls go through OpenRouter. The ModelRouter handles:
- Task-to-model mapping (each node uses a specific model)
- Automatic fallback chains on failure
- Token usage tracking per model
- LangSmith tracing of all invocations

Fallback order (from `llm_registry.FALLBACK_CHAINS`):
```
Claude Sonnet 4.6 → GPT-4.1 → Gemini 2.5 Pro
GPT-4.1           → Claude Sonnet 4.6 → Gemini 2.5 Pro
GPT-4.1-mini      → Gemini 2.5 Flash → Claude Sonnet 4.6
Gemini 2.5 Pro    → Claude Sonnet 4.6 → GPT-4.1
Gemini 2.5 Flash  → GPT-4.1-mini → Claude Sonnet 4.6
```

## Registries

- **LLMRegistry** (`src/models/llm_registry.py`): Task → model mapping, fallback chains, usage tracking.
- **PromptRegistry** (`src/agent/prompts/registry.py`): Task → prompt template. Use `get_prompt(task, **kwargs)` to format. Individual prompts live in `agent/prompts/*.py` and are imported by the registry.
