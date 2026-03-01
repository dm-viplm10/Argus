"""Delete all nodes and relationships in Neo4j."""

from __future__ import annotations

import asyncio

from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.utils.logging import setup_logging


async def main() -> None:
    setup_logging(log_level="INFO", log_format="console")

    settings = get_settings()
    conn = Neo4jConnection(settings)
    await conn.connect()

    try:
        await conn.execute_write("MATCH (n) DETACH DELETE n")
        print("All nodes and relationships deleted from Neo4j.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
