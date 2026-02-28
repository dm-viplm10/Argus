"""Integration tests for the FastAPI application."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked infrastructure."""
    with patch("src.main.Neo4jConnection") as MockNeo4j, \
         patch("src.main.init_schema", new_callable=AsyncMock), \
         patch("src.main.LLMRegistry"), \
         patch("src.main.AsyncRedisSaver", side_effect=ImportError("test")):

        mock_conn = AsyncMock()
        mock_conn.connect = AsyncMock()
        mock_conn.close = AsyncMock()
        mock_conn.health_check = AsyncMock(return_value=True)
        MockNeo4j.return_value = mock_conn

        from src.main import app

        with TestClient(app) as c:
            yield c


def test_health_endpoint(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_start_research(client):
    resp = client.post(
        "/api/v1/research",
        json={
            "target_name": "Timothy Overturf",
            "target_context": "CEO of Sisu Capital",
            "objectives": ["biographical"],
            "max_depth": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "research_id" in data
    assert data["status"] == "queued"


def test_get_research_not_found(client):
    resp = client.get("/api/v1/research/nonexistent")
    assert resp.status_code == 404
