"""Graph Builder node — populates Neo4j with extracted entities and relationships (no LLM)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langgraph.config import get_stream_writer

from src.graph_db.connection import Neo4jConnection
from src.graph_db.queries import (
    MERGE_DOCUMENT,
    MERGE_FUND,
    MERGE_LOCATION,
    MERGE_ORGANIZATION,
    MERGE_PERSON,
    CREATE_RELATIONSHIP_NO_APOC,
    TYPED_RELATIONSHIP_QUERIES,
)
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

ENTITY_TYPE_TO_QUERY = {
    "person": MERGE_PERSON,
    "organization": MERGE_ORGANIZATION,
    "fund": MERGE_FUND,
    "location": MERGE_LOCATION,
    "document": MERGE_DOCUMENT,
}


async def graph_builder_node(
    state: dict[str, Any],
    *,
    neo4j_conn: Neo4jConnection,
) -> dict[str, Any]:
    """Write entities and relationships to Neo4j. Pure code — no LLM calls."""
    writer = get_stream_writer()
    writer({"node": "graph_builder", "status": "started"})

    entities = state.get("entities", [])
    relationships = state.get("relationships", [])
    research_id = state.get("research_id", "unknown")

    # Track only net-new writes this invocation; the state reducer accumulates.
    nodes_created: list[str] = []
    rels_created: list[str] = []

    start = time.monotonic()

    for entity in entities:
        etype = entity.get("type", "").lower()
        query = ENTITY_TYPE_TO_QUERY.get(etype)
        if not query:
            logger.warning("unknown_entity_type", entity_type=etype, name=entity.get("name"))
            continue

        name = entity.get("name", "")
        if not name:
            continue

        props = {**entity.get("attributes", {})}
        props["research_ids"] = [research_id]
        if entity.get("sources"):
            props["source_urls"] = entity["sources"]

        id_param = "name" if etype != "document" else "url"
        id_value = name if etype != "document" else entity.get("attributes", {}).get("url", name)

        try:
            await neo4j_conn.execute_write(query, **{id_param: id_value, "properties": props})
            nodes_created.append(f"{etype}:{name}")
        except Exception as exc:
            logger.error("graph_node_create_failed", entity=name, error=str(exc))

    for rel in relationships:
        from_name = rel.get("source_entity", "")
        to_name = rel.get("target_entity", "")
        rel_type = rel.get("relationship_type", "ASSOCIATED_WITH").upper()

        if not from_name or not to_name:
            continue

        props = {
            "confidence": rel.get("confidence", 0.5),
            "evidence": rel.get("evidence", ""),
            "source_url": rel.get("source_url", ""),
            "research_id": research_id,
        }

        # Use a typed query if the relationship type is known; fall back to
        # ASSOCIATED_WITH (with rel_subtype property) for anything unexpected.
        query = TYPED_RELATIONSHIP_QUERIES.get(rel_type)
        if query:
            try:
                await neo4j_conn.execute_write(
                    query,
                    from_name=from_name,
                    to_name=to_name,
                    properties=props,
                )
                rels_created.append(f"{from_name}-[{rel_type}]->{to_name}")
            except Exception as exc:
                logger.error("graph_rel_create_failed", rel=f"{from_name}->{to_name}", rel_type=rel_type, error=str(exc))
        else:
            logger.warning("unknown_rel_type_fallback", rel_type=rel_type, from_name=from_name, to_name=to_name)
            try:
                await neo4j_conn.execute_write(
                    CREATE_RELATIONSHIP_NO_APOC,
                    from_name=from_name,
                    to_name=to_name,
                    rel_type=rel_type,
                    properties=props,
                )
                rels_created.append(f"{from_name}-[{rel_type}]->{to_name}")
            except Exception as exc:
                logger.error("graph_rel_create_failed", rel=f"{from_name}->{to_name}", rel_type=rel_type, error=str(exc))

    elapsed_ms = int((time.monotonic() - start) * 1000)

    audit = AuditEntry(
        node="graph_builder",
        action="populate_graph",
        timestamp=datetime.now(timezone.utc).isoformat(),
        output_summary=f"Created {len(nodes_created)} nodes, {len(rels_created)} relationships",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "graph_builder",
        "status": "complete",
        "nodes": len(nodes_created),
        "relationships": len(rels_created),
    })

    return {
        "graph_nodes_created": nodes_created,
        "graph_relationships_created": rels_created,
        "phase_complete": True,  # Mark phase as complete after graph building
        "audit_log": [audit.model_dump()],
    }
