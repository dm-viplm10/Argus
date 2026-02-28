"""Schema initialization and optional test data seeding."""

from __future__ import annotations

import asyncio

from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.graph_db.schema import init_schema
from src.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def seed() -> None:
    settings = get_settings()
    conn = Neo4jConnection(settings)
    await conn.connect()

    try:
        await init_schema(conn)
        logger.info("seed_complete")
    finally:
        await conn.close()


if __name__ == "__main__":
    setup_logging(log_level="INFO", log_format="console")
    asyncio.run(seed())
