"""Health and readiness probe endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_neo4j
from src.graph_db.connection import Neo4jConnection

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@router.get("/ready")
async def ready(neo4j: Neo4jConnection = Depends(get_neo4j)) -> dict:
    try:
        ok = await neo4j.health_check()
        return {"status": "ready" if ok else "degraded", "neo4j": ok}
    except Exception as exc:
        return {"status": "not_ready", "error": str(exc)}
