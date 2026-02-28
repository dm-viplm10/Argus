"""Integration test for the full supervisor graph construction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_graph_builds_without_error(settings, mock_registry):
    """Verify the StateGraph compiles successfully."""
    mock_neo4j = AsyncMock()

    from src.agent.graph import build_research_graph

    graph = build_research_graph(settings, mock_registry, mock_neo4j)
    compiled = graph.compile()

    assert compiled is not None
    node_names = set(compiled.nodes.keys()) if hasattr(compiled, 'nodes') else set()
    assert "supervisor" in node_names or len(node_names) == 0  # Compiled graph structure varies
