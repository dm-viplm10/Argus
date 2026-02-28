"""Shared FastAPI dependency injection."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.config import Settings, get_settings
from src.graph_db.connection import Neo4jConnection
from src.models.llm_registry import LLMRegistry
from src.models.model_router import ModelRouter

_neo4j_conn: Neo4jConnection | None = None
_registry: LLMRegistry | None = None
_router: ModelRouter | None = None
_checkpointer: Any = None


def set_neo4j_conn(conn: Neo4jConnection) -> None:
    global _neo4j_conn
    _neo4j_conn = conn


def set_registry(registry: LLMRegistry) -> None:
    global _registry, _router
    _registry = registry
    _router = ModelRouter(registry)


def set_checkpointer(cp: Any) -> None:
    global _checkpointer
    _checkpointer = cp


def get_neo4j() -> Neo4jConnection:
    if _neo4j_conn is None:
        raise RuntimeError("Neo4j not initialized")
    return _neo4j_conn


def get_registry() -> LLMRegistry:
    if _registry is None:
        raise RuntimeError("LLM registry not initialized")
    return _registry


def get_router() -> ModelRouter:
    if _router is None:
        raise RuntimeError("Model router not initialized")
    return _router


def get_checkpointer() -> Any:
    return _checkpointer
