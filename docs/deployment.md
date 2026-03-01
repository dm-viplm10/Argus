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

## Environment Variables

See `.env.example` for all configuration options.

Required:
- `OPENROUTER_API_KEY` — Multi-model LLM access
- `TAVILY_API_KEY` — Web search API

Optional:
- `LANGSMITH_API_KEY` — Tracing & evaluation
- All Neo4j/Redis variables have defaults for Docker

## Cloud Deployment

The system is designed for easy cloud migration:

1. **Neo4j**: Replace with Neo4j Aura (managed)
2. **Redis**: Replace with ElastiCache or Redis Cloud
3. **App**: Single FastAPI container (research runs inline via asyncio; no separate worker)
4. **Secrets**: Use cloud secret manager instead of .env

Update `NEO4J_URI`, `REDIS_URL` in environment to point to
managed services. Everything else works unchanged.
