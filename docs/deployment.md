# Deployment Guide

## Local Development

```bash
# Prerequisites
docker --version    # 24.0+
python --version    # 3.12+

# Setup
cp .env.example .env
# Fill in API keys in .env

make setup    # Build images, init Neo4j schema
make up       # Start all services
make verify   # Verify connectivity
```

## Services

| Service | Port | URL |
|---------|------|-----|
| FastAPI | 8000 | http://localhost:8000/docs |
| Neo4j Browser | 7474 | http://localhost:7474 |
| Neo4j Bolt | 7687 | bolt://localhost:7687 |
| Redis | 6379 | redis://localhost:6379 |
| RedisInsight | 8001 | http://localhost:8001 |
| Streamlit UI | 8501 | http://localhost:8501 |

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | Multi-model LLM access via OpenRouter |
| `TAVILY_API_KEY` | Web search API |

### Neo4j

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://neo4j:7687` | Bolt connection URI |
| `NEO4J_USER` | `neo4j` | Auth username |
| `NEO4J_PASSWORD` | `research_agent_dev` | Auth password (change in production) |

### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |

### Agent Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_SEARCH_DEPTH` | `5` | Max phases per research run |
| `MAX_RESULTS_PER_QUERY` | `10` | Tavily results per query |
| `RATE_LIMIT_SEARCHES_PER_MIN` | `20` | Tavily search rate cap |
| `CONFIDENCE_THRESHOLD` | `0.6` | Minimum fact confidence to accept |
| `MAX_SCRAPE_CONCURRENT` | `5` | Max concurrent web scrape coroutines |
| `RESEARCH_TIMEOUT_SECONDS` | `3600` | Hard wall-clock cap per research run (1 hour). If the pipeline does not complete within this window, the job is marked `failed`. Increase for very deep investigations; decrease for tighter cost control. |

### Security (CORS)

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_ORIGINS` | `["http://localhost:8501","http://localhost:3000"]` | JSON array of origins permitted to make cross-site requests. In production, replace with your frontend origin(s). Using `["*"]` is intentionally rejected — it is incompatible with `allow_credentials=True` per the CORS spec. |

Example production value:
```
ALLOWED_ORIGINS=["https://app.example.com","https://admin.example.com"]
```

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGSMITH_API_KEY` | `""` | LangSmith tracing (optional) |
| `LANGSMITH_PROJECT` | `argus` | LangSmith project name |
| `LANGCHAIN_TRACING_V2` | `true` | Enable LangSmith tracing |
| `LOG_LEVEL` | `INFO` | Structlog level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | `json` | `json` for production, `console` for local dev |

## Health & Readiness Probes

| Endpoint | Use | Description |
|----------|-----|-------------|
| `GET /api/v1/health` | Liveness | Always 200 while the process is alive |
| `GET /api/v1/ready` | Readiness | Checks Neo4j + Redis; returns `{"status": "ready"\|"degraded"\|"not_ready", "neo4j": bool, "redis": bool}` |

Kubernetes example:
```yaml
livenessProbe:
  httpGet:
    path: /api/v1/health
    port: 8000
  initialDelaySeconds: 10
readinessProbe:
  httpGet:
    path: /api/v1/ready
    port: 8000
  initialDelaySeconds: 15
```

## Cloud Deployment

The system is designed for easy cloud migration:

1. **Neo4j**: Replace with Neo4j Aura (managed) — update `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
2. **Redis**: Replace with ElastiCache or Redis Cloud — update `REDIS_URL`
3. **App**: Single FastAPI container — research runs inline via asyncio; no separate worker process required
4. **Secrets**: Use your cloud secret manager instead of `.env`

### Production Checklist

- [ ] Set `NEO4J_PASSWORD` to a strong password (not `research_agent_dev`)
- [ ] Set `ALLOWED_ORIGINS` to your actual frontend origin(s)
- [ ] Set `LOG_FORMAT=json` (default) for structured log ingestion
- [ ] Set `LANGSMITH_API_KEY` if you want LLM execution traces
- [ ] Tune `RESEARCH_TIMEOUT_SECONDS` based on expected investigation depth
- [ ] Verify `/api/v1/ready` returns `"status": "ready"` before routing traffic
