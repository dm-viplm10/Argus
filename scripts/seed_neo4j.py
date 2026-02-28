"""Initialize Neo4j schema — run via `make setup`."""

from __future__ import annotations

import asyncio

from src.graph_db.seed import seed

if __name__ == "__main__":
    asyncio.run(seed())
