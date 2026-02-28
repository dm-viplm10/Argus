# Agent Design Rationale

## Why Supervisor Pattern

The LangGraph Supervisor pattern was chosen because:

1. **Dynamic orchestration**: The supervisor acts as a research director, deciding
   which specialist to invoke next based on current findings. This maps naturally
   to how a real intelligence analyst directs investigation.

2. **Independent sub-agents**: Each node is testable in isolation with mocked inputs.
   The planner can be tested without running searches; the analyzer without real LLM calls.

3. **Complex control flow**: The 11-step decision framework implements loops
   (re-search when findings are thin), conditional branching (skip risk assessment
   if no verified facts), and phase progression.

4. **State accumulation**: The `ResearchState` TypedDict with Annotated reducers
   means each node contributes to cumulative state without overwriting prior work.

## Why Custom StateGraph over create_supervisor

The `langgraph-supervisor` library (v0.0.31) is designed for simple
agent-as-tool handoff patterns. Our requirements exceed this:

- Custom state with 30+ research-specific fields
- Deterministic routing logic (not LLM-decided every time)
- Phase-based progression with budget tracking
- Specific instructions passed to each sub-agent per invocation

The LangChain team recommends custom supervisor logic for complex use cases.

## Model Selection Rationale

### Claude Sonnet 4.6 (Supervisor, Planner, Verifier, Synthesizer)
Best structured reasoning. Cross-referencing requires careful logical comparison
of claims across sources. Report synthesis needs the best writing quality.

### GPT-4.1-mini (Query Refiner, Search Agent)
Fast and cheap for mechanical tasks. Query refinement and tool-loop execution
don't need deep reasoning — they need speed and reliability.

### Gemini 2.5 Pro (Analyzer)
1.05M token context window. When processing multiple scraped pages simultaneously,
Gemini handles the volume better than any other model. Strong at structured
entity extraction.

### Grok 3 (Risk Assessor)
Least filtered major model. For risk assessment, we want a model willing to
surface uncomfortable truths and speculative connections without excessive hedging.

## Checkpointing Strategy

Using `langgraph-checkpoint-redis` means:
- Each node completion writes a checkpoint
- Worker crashes resume from last completed node
- Status queries read checkpoints directly
- "Time travel" debugging possible

## Streaming Architecture

Each node uses `get_stream_writer()` to emit progress events:
```python
writer({"node": "analyzer", "phase": 2, "facts_found": 15})
```

These flow through FastAPI's SSE endpoint for real-time frontend updates.
