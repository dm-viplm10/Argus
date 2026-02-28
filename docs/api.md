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

### GET /api/v1/graph/{id}/export?format=graphml

Export graph in JSON or GraphML format.

### POST /api/v1/evaluate

Run evaluation comparing research output to ground truth.

**Request:**
```json
{
  "research_id": "uuid",
  "ground_truth_file": "timothy_overturf.json"
}
```

### GET /api/v1/health

Health probe. Returns `{"status": "healthy"}`.

### GET /api/v1/ready

Readiness probe. Checks Neo4j connectivity.
