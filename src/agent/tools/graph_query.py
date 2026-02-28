"""Neo4j graph query tool for agents to query their own identity graph."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from src.graph_db.connection import Neo4jConnection


class GraphQueryTool(BaseTool):
    """Query the Neo4j identity graph for entities and relationships.

    Allows the agent to check what entities and relationships have
    already been stored during the current research session.
    """

    name: str = "graph_query"
    description: str = (
        "Query the identity graph for known entities and relationships. "
        "Input should be a person or organization name. "
        "Returns known connections and attributes."
    )
    neo4j_conn: Any = None

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str) -> str:
        raise NotImplementedError("Use async version")

    async def _arun(self, entity_name: str) -> str:
        if self.neo4j_conn is None:
            return "[Graph database not available]"

        try:
            results = await self.neo4j_conn.execute_read(
                """
                MATCH (n {name: $name})-[r]-(connected)
                RETURN n.name AS source, type(r) AS rel_type,
                       connected.name AS target, labels(connected) AS target_labels
                LIMIT 50
                """,
                name=entity_name,
            )
            if not results:
                return f"No graph data found for '{entity_name}'"

            lines = [f"Graph connections for '{entity_name}':"]
            for rec in results:
                lines.append(
                    f"  -[{rec['rel_type']}]-> {rec['target']} ({', '.join(rec.get('target_labels', []))})"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"[Graph query error: {exc}]"
