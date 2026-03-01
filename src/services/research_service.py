"""Research job lifecycle orchestration service."""

from __future__ import annotations

from typing import Any

from src.config import Settings
from src.graph_db.connection import Neo4jConnection
from src.models.llm_registry import LLMRegistry
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ResearchService:
    """Orchestrates research job creation, status tracking, and result retrieval."""

    def __init__(
        self,
        settings: Settings,
        registry: LLMRegistry,
        neo4j_conn: Neo4jConnection,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._neo4j = neo4j_conn

    async def create_job(
        self,
        research_id: str,
        target_name: str,
        target_context: str,
        objectives: list[str],
        max_depth: int = 5,
    ) -> dict[str, Any]:
        """Initialize a new research job and run it in the background."""
        logger.info(
            "research_job_created",
            research_id=research_id,
            target=target_name,
        )
        return {
            "research_id": research_id,
            "status": "queued",
            "target_name": target_name,
        }
