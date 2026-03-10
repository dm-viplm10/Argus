# API Documentation

Auto-generated OpenAPI/Swagger docs available at `http://localhost:8000/docs` when the app is running.

## Allowed Methods & Headers

The API is served with a CORS policy that restricts:
- **Methods**: `GET`, `POST`, `DELETE`
- **Headers**: `Content-Type`, `X-Request-ID`
- **Origins**: configured via `ALLOWED_ORIGINS` in `.env` (default: `http://localhost:8501`, `http://localhost:3000`)

Every response includes an `X-Request-ID` header (echoed from the request or auto-generated).

## Endpoints

### POST /api/v1/research

Start a new research investigation.

**Request:**
```json
{
  "target_name": "Timothy Overturf",
  "target_context": "CEO of Sisu Capital",
  "objectives": ["biographical", "financial", "risk_assessment", "connections"],
  "max_depth": 5
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target_name` | string | — | Full name of the investigation target |
| `target_context` | string | `""` | Brief context (role, organisation) |
| `objectives` | array | `[]` | Research objectives |
| `max_depth` | integer\|null | `null` | Max research phases. `null` = dynamic (Phase Strategist decides) |

**Response (202):**
```json
{
  "research_id": "uuid",
  "status": "queued",
  "created_at": "2026-02-28T00:00:00Z"
}
```

The job runs as an inline asyncio task inside `ResearchService`. A hard wall-clock timeout (`RESEARCH_TIMEOUT_SECONDS`, default 3 600 s) is applied; if it fires the job is marked `failed`.

### DELETE /api/v1/research/{id}/cancel

Cancel a running or queued job. No-op if already terminal (`completed`, `failed`, `cancelled`).

**Response (200):**
```json
{ "research_id": "uuid", "status": "cancelled" }
```

### GET /api/v1/research/{id}

Get full research results including the final report.

**Response fields:** `research_id`, `status`, `target_name`, `target_context`, `final_report`, `facts_count`, `entities_count`, `risk_flags_count`, `overall_risk_score`, `audit_log`.

### GET /api/v1/research/{id}/status

Real-time status with counts of facts, entities, risk flags, and the current graph node being executed.

**Response fields:** `status`, `current_phase`, `max_phases`, `facts_extracted`, `entities_discovered`, `verified_facts`, `risk_flags`, `graph_nodes`, `searches_executed`, `iteration_count`, `errors`, `current_node`, `audit_log`.

### GET /api/v1/research/{id}/stream

SSE endpoint. Streams raw progress events mapped from LangGraph `astream_events` by `src/api/v1/sse_mapper.to_sse_event()`. Each message uses standard SSE format:

```
event: node_start
data: {"node": "search_and_analyze"}

event: node_end
data: {"node": "search_and_analyze", "extracted_facts": 12, "entities": 5}

event: token
data: {"node": "synthesizer", "content": "## Executive Summary\n..."}

event: thinking
data: {"node": "supervisor", "content": "...extended thinking block..."}

event: tool_start
data: {"node": "search_and_analyze", "tool": "tavily_search", "input": "Timothy Overturf CEO"}

event: tool_end
data: {"node": "search_and_analyze", "tool": "tavily_search", "output": "[{\"url\": ..."}

event: done
data: {"status": "completed"}
```

**Event type reference:**

| Event | When | Key fields |
|---|---|---|
| `node_start` | Graph node begins execution | `node` |
| `node_end` | Graph node finishes | `node`, counts for facts/entities/risk/queries/phases, `has_report`, `risk_score` |
| `token` | LLM text token streamed | `node`, `content` |
| `thinking` | Claude extended thinking block | `node`, `content` |
| `tool_start` | Tool invocation begins | `node`, `tool`, `input` (≤ 500 chars) |
| `tool_end` | Tool invocation completes | `node`, `tool`, `output` (≤ 500 chars) |
| `done` | Job reached terminal state | `status` |

The stream closes when the job reaches a terminal state (`completed`, `failed`, `cancelled`) or the hard timeout fires.

### GET /api/v1/graph/{id}

Identity graph as JSON (D3-compatible nodes + edges).

**Response schema:**
```json
{
  "nodes": [{"id": "...", "labels": ["Person"], "properties": {...}}],
  "edges": [{"source": "...", "target": "...", "type": "WORKS_AT", "properties": {...}}]
}
```

Returns **404** if no graph data exists for the research ID.

### GET /api/v1/graph/{id}/export?format=json|graphml|png|jpeg

Export the identity graph. Both `GET /graph/{id}` and `GET /graph/{id}/export` use a shared `_fetch_graph_data()` helper internally so Neo4j is queried once per request.

- `format=json` — same JSON structure as `GET /api/v1/graph/{id}`
- `format=graphml` — GraphML XML (importable into Gephi, Cytoscape)
- `format=png` / `format=jpeg` — rendered network image via matplotlib (Agg backend, singleton-initialised)

### POST /api/v1/evaluate

Run evaluation of a completed research job against ground truth. Compares research output (facts, entities, relationships, risk flags) to a curated ground-truth file and computes metrics. Optionally uses an LLM judge (GPT-4.1) to score each metric and produce reasoning.

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|--------------|
| `research_id` | string | `""` | ID of the completed research run to evaluate. Required unless `state` is provided. |
| `ground_truth_file` | string | `"timothy_overturf.json"` | Filename in the backend `ground_truth` directory (e.g. under `src/evaluation/ground_truth/`). |
| `use_llm_judge` | boolean | `true` | When true, each metric is scored by an LLM (GPT-4.1) and the response includes per-metric reasoning and a full markdown evaluation report. |
| `state` | object | `null` | Optional. Inline research state (e.g. from a completed run). If set, `research_id` is not required; used for testing or when state is provided out-of-band. |

**State resolution (when `state` is not provided):**

1. **Redis eval checkpoint** — Key `argus:evalstate:{research_id}` (written when the run completes; 30-day TTL).
2. **In-memory** — `_jobs[research_id]["state"]` (same process only).
3. **LangGraph checkpointer** — Fallback if the eval key was not written.

Research must be in `completed` status; otherwise the API returns 400.

**Response (200):**

```json
{
  "evaluation_id": "uuid",
  "research_id": "uuid",
  "metrics": {
    "fact_precision": 1.0,
    "network_fidelity": 0.72,
    "risk_detection_rate": 0.72,
    "depth_score": 0.4,
    "efficiency": 1.0,
    "source_quality": 1.0,
    "metric_reasoning": {
      "network_fidelity": "Per-metric reasoning from LLM judge...",
      "risk_detection_rate": "...",
      "depth_score": "...",
      "efficiency": "...",
      "source_quality": "...",
      "fact_precision": "..."
    }
  },
  "summary": "Evaluation for target: ... Fact Precision: 100.0% ...",
  "evaluation_report": "# Evaluation Report: ...\n\n## Summary\n..."
}
```

- **metrics**: Fact precision (verified_facts / (verified_facts + unverified_claims)), network fidelity, risk detection rate, depth score, efficiency, source quality; `metric_reasoning` is populated when `use_llm_judge` is true.
- **evaluation_report**: Full markdown report (source of truth, summary, per-metric reasoning). Omitted or minimal when `use_llm_judge` is false.

**Errors:**

- **400** — Missing both `research_id` and `state`; or research not completed.
- **404** — Research job not found; or ground truth file not found.
- **422** — Research state not available (e.g. Redis checkpointing not enabled or state expired).

### GET /api/v1/evaluate/{evaluation_id}/results

Retrieve a previously run evaluation by ID. Returns the same structure as the POST response.

- Returns **404** if the evaluation ID is unknown.
- Results are stored in an in-process `OrderedDict` capped at **1 000 entries** (oldest evicted first when the cap is exceeded). Results do not survive process restarts.

### GET /api/v1/health

Liveness probe. Always returns 200 while the process is alive.

```json
{ "status": "healthy" }
```

### GET /api/v1/ready

Readiness probe. Checks both Neo4j and Redis connectivity.

**Response (200) — both stores healthy:**
```json
{ "status": "ready", "neo4j": true, "redis": true }
```

**Response (200) — at least one store degraded:**
```json
{ "status": "degraded", "neo4j": true, "redis": false }
```

**Response (200) — Neo4j raised an exception:**
```json
{ "status": "not_ready", "neo4j": false, "redis": false, "error": "connection refused" }
```

A `"degraded"` status is returned with HTTP 200 (not 5xx) so that Kubernetes / ECS readiness probes can observe the degradation and stop routing without crashing the pod. Use `"status" == "ready"` in your orchestrator's readiness gate expression.
