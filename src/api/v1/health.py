"""Health and readiness probe endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.api.dependencies import get_neo4j, get_redis

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from src.graph_db.connection import Neo4jConnection

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@router.get("/ready")
async def ready(
    neo4j: Neo4jConnection = Depends(get_neo4j),
    redis: aioredis.Redis | None = Depends(get_redis),
) -> dict:
    """Readiness probe — checks both Neo4j and Redis connectivity.

    Returns 200 with ``"status": "ready"`` only when both stores are reachable.
    Returns 200 with ``"status": "degraded"`` when either store fails, so
    orchestrators (k8s, ECS) can observe the degradation without crashing the pod.
    """
    neo4j_ok = False
    try:
        neo4j_ok = await neo4j.health_check()
    except Exception as exc:
        return {"status": "not_ready", "neo4j": False, "redis": False, "error": str(exc)}

    redis_ok = False
    if redis is not None:
        try:
            await redis.ping()
            redis_ok = True
        except Exception:
            pass

    all_ok = neo4j_ok and redis_ok
    return {
        "status": "ready" if all_ok else "degraded",
        "neo4j": neo4j_ok,
        "redis": redis_ok,
    }
