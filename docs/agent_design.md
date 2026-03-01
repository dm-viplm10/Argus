# Agent Design Rationale

## Agent Abstractions (SOLID)

All graph nodes extend one of three base classes in `src/agent/base.py`:

- **BaseAgent** — Abstract base. Each agent implements `async def run(state) -> dict`.
- **StructuredOutputAgent** — For nodes using `ModelRouter.invoke()` with Pydantic schemas.
  Dependencies: `router`, `prompt_registry`. Used by Planner, Supervisor, Phase Strategist, Query Refiner, Risk Assessor, Synthesizer.
- **ReActAgent** — For ReAct loops (search + tools + structured submit).
  Dependencies: `registry`, `settings`, `prompt_registry`. Used by Search & Analyze, Verifier.
- **ToolNode** — For pure code (no LLM). Dependencies: `neo4j_conn`. Used by Graph Builder.

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

### GPT-4.1 (Supervisor, Phase Strategist, Risk Assessor)
Fast orchestration and routing. Phase strategy and risk assessment benefit from
solid reasoning without needing the heaviest models.

### Claude Sonnet 4.6 (Planner, Synthesizer)
Best structured reasoning for plan generation. Report synthesis needs the best
writing quality.

### GPT-4.1-mini (Query Refiner)
Fast and cheap for mechanical query refinement.

### Gemini 2.5 Flash (Search & Analyze)
Efficient ReAct loop for search + scrape + structured extraction in one pass.
Good balance of speed and extraction quality.

### Gemini 2.5 Pro (Verifier)
Strong reasoning for active fact verification via web search. Handles nuanced
cross-referencing and confidence assignment.

## Checkpointing Strategy

Using `langgraph-checkpoint-redis` means:
- Each node completion writes a checkpoint
- Process restarts resume from last completed node
- Status queries read checkpoints directly
- "Time travel" debugging possible

## Streaming Architecture

Each agent uses `get_stream_writer()` inside `run()` to emit progress events:
```python
writer = get_stream_writer()
writer({"node": "search_and_analyze", "status": "started", "phase": 2})
writer({"node": "search_and_analyze", "status": "complete", "facts": 15})
```

These flow through `astream_events` to the FastAPI SSE endpoint for real-time frontend updates.
