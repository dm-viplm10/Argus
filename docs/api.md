# API Documentation

Auto-generated OpenAPI/Swagger docs available at `http://localhost:8000/docs` when the app is running.

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

**Response (202):**
```json
{
  "research_id": "uuid",
  "status": "queued",
  "created_at": "2026-02-28T00:00:00Z"
}
```

### GET /api/v1/research/{id}

Get full research results including the final report.

### GET /api/v1/research/{id}/status

Real-time status with counts of facts, entities, risk flags.

### GET /api/v1/research/{id}/stream

SSE endpoint streaming progress events:
```
event: status
data: {"status": "running", "current_phase": 2, "facts": 12, "entities": 5}

event: done
data: {"status": "completed"}
```

### GET /api/v1/graph/{id}

Identity graph as JSON (D3-compatible nodes + edges).

### GET /api/v1/graph/{id}/export?format=json|graphml|png|jpeg

Export the identity graph. Use `format=json` or `format=graphml` for data; use `format=png` or `format=jpeg` to download the graph as an image (requires matplotlib in the backend).

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

- **metrics**: Fact precision (from state: verified_facts / (verified_facts + unverified_claims)), network fidelity, risk detection rate, depth score, efficiency, source quality; `metric_reasoning` is populated when `use_llm_judge` is true.
- **evaluation_report**: Full markdown report (source of truth, summary, per-metric reasoning). Omitted or minimal when `use_llm_judge` is false.

**Errors:**

- **400** — Missing both `research_id` and `state`; or research not completed.
- **404** — Research job not found; or ground truth file not found.
- **422** — Research state not available (e.g. Redis checkpointing not enabled or state expired).

### GET /api/v1/evaluate/{evaluation_id}/results

Retrieve a previously run evaluation by ID. Returns the same structure as the POST response. Returns **404** if the evaluation ID is unknown (results are stored in memory for the lifetime of the process).

### GET /api/v1/health

Health probe. Returns `{"status": "healthy"}`.

### GET /api/v1/ready

Readiness probe. Checks Neo4j connectivity.
