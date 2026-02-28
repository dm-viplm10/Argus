"""Neo4j graph CRUD and query service."""

from __future__ import annotations

from typing import Any

from src.graph_db.connection import Neo4jConnection
from src.graph_db.queries import (
    DELETE_RESEARCH_DATA,
    FULL_GRAPH_JSON,
    GRAPH_FOR_RESEARCH,
    RISK_HOTSPOTS,
    SHORTEST_PATH,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class GraphService:
    """High-level graph operations for the identity graph."""

    def __init__(self, neo4j_conn: Neo4jConnection) -> None:
        self._conn = neo4j_conn

    async def get_research_graph(self, research_id: str) -> dict[str, Any]:
        results = await self._conn.execute_read(
            GRAPH_FOR_RESEARCH, research_id=research_id
        )
        if not results:
            return {"nodes": [], "edges": []}
        return dict(results[0])

    async def get_full_graph(self) -> dict[str, Any]:
        results = await self._conn.execute_read(FULL_GRAPH_JSON)
        if not results:
            return {"nodes": [], "edges": []}
        return dict(results[0])

    async def get_shortest_path(self, from_name: str, to_name: str) -> list[dict]:
        return await self._conn.execute_read(
            SHORTEST_PATH, from_name=from_name, to_name=to_name
        )

    async def get_risk_hotspots(self) -> list[dict]:
        return await self._conn.execute_read(RISK_HOTSPOTS)

    async def delete_research_data(self, research_id: str) -> None:
        await self._conn.execute_write(
            DELETE_RESEARCH_DATA, research_id=research_id
        )
        logger.info("research_data_deleted", research_id=research_id)
