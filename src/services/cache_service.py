"""Redis caching layer for search results and intermediate data."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.utils.logging import get_logger

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = get_logger(__name__)


class CacheService:
    """Redis-backed cache for search results and research state.

    Accepts the shared application Redis client created at startup — it does
    *not* own the connection and does not close it. Callers must ensure the
    client is initialised before constructing this service.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._client = redis_client

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        await self._client.set(key, serialized, ex=ttl)

    async def get_cached_search(self, query: str) -> list[dict] | None:
        """Check if a search query result is cached (1 hour TTL)."""
        return await self.get(f"search:{query}")

    async def cache_search(self, query: str, results: list[dict]) -> None:
        await self.set(f"search:{query}", results, ttl=3600)
