"""Integration tests for Neo4j (requires running Neo4j instance)."""

from __future__ import annotations

import pytest

# These tests require a running Neo4j instance.
# Run with: docker compose up neo4j -d && pytest tests/integration/test_neo4j.py

pytestmark = pytest.mark.skipif(
    True,  # Skip by default; set to False when Neo4j is running
    reason="Requires running Neo4j instance",
)


@pytest.mark.asyncio
async def test_neo4j_connection():
    from src.config import get_settings
    from src.graph_db.connection import Neo4jConnection

    settings = get_settings()
    conn = Neo4jConnection(settings)
    await conn.connect()
    assert await conn.health_check() is True
    await conn.close()


@pytest.mark.asyncio
async def test_neo4j_schema_init():
    from src.config import get_settings
    from src.graph_db.connection import Neo4jConnection
    from src.graph_db.schema import init_schema

    settings = get_settings()
    conn = Neo4jConnection(settings)
    await conn.connect()
    await init_schema(conn)
    await conn.close()
