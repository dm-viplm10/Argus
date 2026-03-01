"""Shared test fixtures."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    """Set required environment variables for tests."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture
def settings():
    from src.config import Settings

    return Settings(
        OPENROUTER_API_KEY="test-key",
        TAVILY_API_KEY="test-tavily-key",
        LANGSMITH_API_KEY="",
        LANGCHAIN_TRACING_V2=False,
    )


@pytest.fixture
def mock_registry(settings):
    """LLM registry with mocked models."""
    from src.models.llm_registry import LLMRegistry

    with patch.object(LLMRegistry, "__init__", lambda self, s: None):
        registry = LLMRegistry.__new__(LLMRegistry)
        registry._settings = settings
        registry._models = {}
        registry._slug_cache = {}
        registry._call_stats = {}

        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(return_value=MagicMock(content="test response"))
        mock_model.model_name = "test-model"

        for task in [
            "supervisor", "planner", "query_refiner", "search_and_analyze",
            "verifier", "risk_assessor", "synthesizer",
        ]:
            registry._models[task] = mock_model
            registry._call_stats[task] = {"calls": 0, "tokens": 0, "cost": 0.0}

        return registry


@pytest.fixture
def mock_router(mock_registry):
    from src.models.model_router import ModelRouter

    return ModelRouter(mock_registry)


@pytest.fixture
def sample_state() -> dict:
    """A sample research state for testing."""
    return {
        "research_id": "test-123",
        "target_name": "Timothy Overturf",
        "target_context": "CEO of Sisu Capital",
        "research_objectives": ["biographical", "financial", "risk_assessment"],
        "current_phase": 1,
        "max_phases": 5,
        "iteration_count": 0,
        "phase_complete": False,
        "research_plan": [],
        "pending_queries": [],
        "search_queries_executed": [],
        "urls_visited": set(),
        "extracted_facts": [],
        "entities": [],
        "relationships": [],
        "contradictions": [],
        "verified_facts": [],
        "unverified_claims": [],
        "risk_flags": [],
        "overall_risk_score": None,
        "graph_nodes_created": [],
        "graph_relationships_created": [],
        "final_report": None,
        "total_tokens_used": 0,
        "total_cost_usd": 0.0,
        "errors": [],
        "audit_log": [],
    }
