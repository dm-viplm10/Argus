"""Neo4j schema initialization: constraints, indexes, and node/relationship definitions."""

from __future__ import annotations

from src.graph_db.connection import Neo4jConnection
from src.utils.logging import get_logger

logger = get_logger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT org_name IF NOT EXISTS FOR (o:Organization) REQUIRE o.name IS UNIQUE",
    "CREATE CONSTRAINT fund_name IF NOT EXISTS FOR (f:Fund) REQUIRE f.name IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:Event) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT location_name IF NOT EXISTS FOR (l:Location) REQUIRE l.name IS UNIQUE",
    "CREATE CONSTRAINT document_url IF NOT EXISTS FOR (d:Document) REQUIRE d.url IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX person_search IF NOT EXISTS FOR (p:Person) ON (p.name)",
    "CREATE INDEX org_search IF NOT EXISTS FOR (o:Organization) ON (o.name)",
    "CREATE INDEX fund_search IF NOT EXISTS FOR (f:Fund) ON (f.name)",
    (
        "CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS "
        "FOR (n:Person|Organization|Fund) ON EACH [n.name, n.aliases]"
    ),
]


async def init_schema(conn: Neo4jConnection) -> None:
    """Create all constraints and indexes on the Neo4j database."""
    for stmt in CONSTRAINTS:
        try:
            await conn.execute_write(stmt)
        except Exception as exc:
            logger.warning("constraint_create_skipped", statement=stmt, error=str(exc))

    for stmt in INDEXES:
        try:
            await conn.execute_write(stmt)
        except Exception as exc:
            logger.warning("index_create_skipped", statement=stmt, error=str(exc))

    logger.info("neo4j_schema_initialized")
