"""Unit tests for the graph service."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.graph_service import GraphService


@pytest.fixture
def mock_neo4j():
    conn = AsyncMock()
    conn.execute_read = AsyncMock(return_value=[])
    conn.execute_write = AsyncMock(return_value=[])
    return conn


@pytest.mark.asyncio
async def test_get_research_graph_empty(mock_neo4j):
    service = GraphService(mock_neo4j)
    result = await service.get_research_graph("test-123")
    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_get_risk_hotspots(mock_neo4j):
    mock_neo4j.execute_read = AsyncMock(return_value=[
        {"name": "Test Person", "connections": 5, "rel_types": ["WORKS_AT"]}
    ])
    service = GraphService(mock_neo4j)
    result = await service.get_risk_hotspots()
    assert len(result) == 1
    assert result[0]["name"] == "Test Person"


@pytest.mark.asyncio
async def test_delete_research_data(mock_neo4j):
    service = GraphService(mock_neo4j)
    await service.delete_research_data("test-123")
    mock_neo4j.execute_write.assert_called_once()
