"""Graph API endpoints — retrieve and export identity graphs."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

try:
    from neo4j.time import DateTime, Date, Time, Duration
    _NEO4J_TEMPORAL = (DateTime, Date, Time, Duration)
except ImportError:
    _NEO4J_TEMPORAL = ()

from src.api.dependencies import get_neo4j
from src.api.v1.schemas.graph import GraphEdge, GraphNode, GraphResponse
from src.graph_db.connection import Neo4jConnection
from src.graph_db.queries import GRAPH_FOR_RESEARCH
from src.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


def _sanitize_neo4j_value(value: Any) -> Any:
    """Convert Neo4j temporal types to JSON-serializable values."""
    if _NEO4J_TEMPORAL and isinstance(value, _NEO4J_TEMPORAL):
        return str(value)
    if isinstance(value, dict):
        return {k: _sanitize_neo4j_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_neo4j_value(v) for v in value]
    return value


@router.get("/{research_id}", response_model=GraphResponse)
async def get_graph(
    research_id: str,
    neo4j: Neo4jConnection = Depends(get_neo4j),
) -> GraphResponse:
    """Get the identity graph for a research job as JSON (D3-compatible)."""
    try:
        results = await neo4j.execute_read(
            GRAPH_FOR_RESEARCH,
            research_id=research_id,
        )
    except Exception as exc:
        logger.error("graph_query_failed", research_id=research_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Graph query failed")

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    if results:
        record = results[0] if results else {}
        for n in record.get("nodes", []):
            if n and n.get("id"):
                nodes.append(GraphNode(
                    id=str(n["id"]),
                    labels=n.get("labels", []),
                    properties=_sanitize_neo4j_value(n.get("properties", {})),
                ))
        for e in record.get("edges", []):
            if e and e.get("source") and e.get("target"):
                edges.append(GraphEdge(
                    source=str(e["source"]),
                    target=str(e["target"]),
                    type=e.get("type", "UNKNOWN"),
                    properties=_sanitize_neo4j_value(e.get("properties", {})),
                ))

    return GraphResponse(
        research_id=research_id,
        nodes=nodes,
        edges=edges,
        node_count=len(nodes),
        edge_count=len(edges),
    )


@router.get("/{research_id}/export")
async def export_graph(
    research_id: str,
    format: Literal["json", "graphml"] = "json",
    neo4j: Neo4jConnection = Depends(get_neo4j),
) -> Response:
    """Export the identity graph in JSON or GraphML format."""
    graph_data = await get_graph(research_id, neo4j)

    if format == "json":
        content = json.dumps(graph_data.model_dump(), indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=graph_{research_id}.json"},
        )

    # GraphML export
    graphml = _to_graphml(graph_data)
    return Response(
        content=graphml,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=graph_{research_id}.graphml"},
    )


def _to_graphml(graph: GraphResponse) -> str:
    """Convert graph response to GraphML XML format."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphstruct.org/graphml">',
        '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="type" for="edge" attr.name="type" attr.type="string"/>',
        '  <graph id="G" edgedefault="directed">',
    ]

    for node in graph.nodes:
        label = node.labels[0] if node.labels else "Unknown"
        name = node.properties.get("name", node.id)
        lines.append(f'    <node id="{node.id}">')
        lines.append(f'      <data key="label">{_xml_escape(name)} ({label})</data>')
        lines.append("    </node>")

    for i, edge in enumerate(graph.edges):
        lines.append(f'    <edge id="e{i}" source="{edge.source}" target="{edge.target}">')
        lines.append(f'      <data key="type">{_xml_escape(edge.type)}</data>')
        lines.append("    </edge>")

    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines)


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
