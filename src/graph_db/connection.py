"""Async Neo4j driver management with connection pooling and health checks."""

from __future__ import annotations

from neo4j import AsyncGraphDatabase, AsyncDriver

from src.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class Neo4jConnection:
    """Manages the async Neo4j driver lifecycle.

    Provides connection pooling, health checks, and graceful shutdown.
    Designed for use with FastAPI lifespan events.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: AsyncDriver | None = None

    async def connect(self) -> None:
        self._driver = AsyncGraphDatabase.driver(
            self._settings.NEO4J_URI,
            auth=(self._settings.NEO4J_USER, self._settings.NEO4J_PASSWORD),
            max_connection_pool_size=50,
        )
        await self.health_check()
        logger.info("neo4j_connected", uri=self._settings.NEO4J_URI)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("neo4j_disconnected")

    async def health_check(self) -> bool:
        driver = self.driver
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS ok")
            record = await result.single()
            return record is not None and record["ok"] == 1

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialized — call connect() first")
        return self._driver

    async def execute_read(self, query: str, **params: object) -> list[dict]:
        async with self.driver.session() as session:
            result = await session.run(query, params)
            return [dict(record) async for record in result]

    async def execute_write(self, query: str, **params: object) -> list[dict]:
        async with self.driver.session() as session:
            result = await session.run(query, params)
            return [dict(record) async for record in result]
