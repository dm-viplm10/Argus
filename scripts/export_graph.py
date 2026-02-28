"""Export identity graph from Neo4j to JSON file."""

from __future__ import annotations

import asyncio
import json
import sys

from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.graph_db.queries import FULL_GRAPH_JSON
from src.utils.logging import setup_logging


async def main() -> None:
    setup_logging(log_level="INFO", log_format="console")
    settings = get_settings()

    conn = Neo4jConnection(settings)
    await conn.connect()

    try:
        results = await conn.execute_read(FULL_GRAPH_JSON)
        if not results:
            print("No graph data found.")
            sys.exit(0)

        output = results[0] if results else {"nodes": [], "edges": []}
        filename = "graph_export.json"
        with open(filename, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Graph exported to {filename}")
        print(f"  Nodes: {len(output.get('nodes', []))}")
        print(f"  Edges: {len(output.get('edges', []))}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
