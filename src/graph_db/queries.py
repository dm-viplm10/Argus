"""Parameterized Cypher query templates for the identity graph."""

MERGE_PERSON = """
MERGE (p:Person {name: $name})
SET p += $properties, p.last_updated = datetime()
RETURN p
"""

MERGE_ORGANIZATION = """
MERGE (o:Organization {name: $name})
SET o += $properties, o.last_updated = datetime()
RETURN o
"""

MERGE_FUND = """
MERGE (f:Fund {name: $name})
SET f += $properties, f.last_updated = datetime()
RETURN f
"""

MERGE_EVENT = """
MERGE (e:Event {event_id: $event_id})
SET e += $properties, e.last_updated = datetime()
RETURN e
"""

MERGE_LOCATION = """
MERGE (l:Location {name: $name})
SET l += $properties
RETURN l
"""

MERGE_DOCUMENT = """
MERGE (d:Document {url: $url})
SET d += $properties
RETURN d
"""

# The APOC-based CREATE_RELATIONSHIP was removed — it required the APOC plugin
# and was never called at runtime (graph_builder.py uses TYPED_RELATIONSHIP_QUERIES).
# TYPED_RELATIONSHIP_QUERIES is the authoritative implementation; see below.

# Typed-relationship templates — the only relationship creation path used at runtime.
# Each entry matches one relationship type the search_and_analyze prompt produces.
_TYPED_REL_TEMPLATE = "MATCH (a {{name: $from_name}}), (b {{name: $to_name}}) MERGE (a)-[r:{rel_type}]->(b) SET r += $properties RETURN r"

TYPED_RELATIONSHIP_QUERIES: dict[str, str] = {
    rel_type: _TYPED_REL_TEMPLATE.format(rel_type=rel_type)
    for rel_type in (
        "WORKS_AT",
        "OWNS",
        "BOARD_MEMBER_OF",
        "ASSOCIATED_WITH",
        "LITIGATED",
        "MANAGES",
        "INVESTED_IN",
        "LOCATED_IN",
        "MENTIONED_IN",
    )
}

SHORTEST_PATH = """
MATCH path = shortestPath((a:Person {name: $from_name})-[*..6]-(b {name: $to_name}))
RETURN path
"""

SUBGRAPH_FOR_PERSON = """
MATCH (center:Person {name: $name})-[r*1..3]-(connected)
RETURN center, r, connected
"""

FULL_GRAPH_JSON = """
MATCH (n)-[r]->(m)
RETURN
    collect(DISTINCT {id: elementId(n), labels: labels(n), properties: properties(n)}) +
    collect(DISTINCT {id: elementId(m), labels: labels(m), properties: properties(m)}) AS nodes,
    collect({source: elementId(n), target: elementId(m), type: type(r), properties: properties(r)}) AS edges
"""

GRAPH_FOR_RESEARCH = """
MATCH (n)
WHERE $research_id IN n.research_ids
OPTIONAL MATCH (n)-[r]->(m)
WHERE $research_id IN m.research_ids
RETURN
    collect(DISTINCT {id: elementId(n), labels: labels(n), properties: properties(n)}) AS nodes,
    collect(DISTINCT {source: elementId(n), target: elementId(m), type: type(r), properties: properties(r)}) AS edges
"""

RISK_HOTSPOTS = """
MATCH (p:Person)-[r]-(e)
WHERE r.confidence < 0.5 OR e:Event
RETURN p.name AS name, count(r) AS connections, collect(DISTINCT type(r)) AS rel_types
ORDER BY connections DESC
LIMIT 20
"""

DELETE_RESEARCH_DATA = """
MATCH (n)
WHERE $research_id IN n.research_ids
DETACH DELETE n
"""
