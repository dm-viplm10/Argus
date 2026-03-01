"""LangGraph supervisor graph definition — wires all nodes together with routing."""

from __future__ import annotations

import functools
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.edges import route_from_supervisor
from src.agent.nodes.graph_builder import graph_builder_node
from src.agent.nodes.planner import planner_node
from src.agent.nodes.query_refiner import query_refiner_node
from src.agent.nodes.risk_assessor import risk_assessor_node
from src.agent.nodes.search_and_analyze import search_and_analyze_node
from src.agent.nodes.synthesizer import synthesizer_node
from src.agent.nodes.verifier import verifier_node
from src.agent.state import ResearchState
from src.agent.supervisor import supervisor_node
from src.config import Settings
from src.graph_db.connection import Neo4jConnection
from src.models.llm_registry import LLMRegistry
from src.models.model_router import ModelRouter


def build_research_graph(
    settings: Settings,
    registry: LLMRegistry,
    neo4j_conn: Neo4jConnection,
) -> StateGraph:
    """Build the complete research supervisor StateGraph.

    Each node is a partial-applied async function that receives
    shared dependencies (router, registry, neo4j_conn) via closure.
    """
    router = ModelRouter(registry)

    # Bind dependencies to node functions
    _supervisor = functools.partial(supervisor_node, router=router)
    _planner = functools.partial(planner_node, router=router)
    _query_refiner = functools.partial(query_refiner_node, router=router)
    _search_and_analyze = functools.partial(
        search_and_analyze_node, registry=registry, settings=settings
    )
    _verifier = functools.partial(verifier_node, registry=registry, settings=settings)
    _risk_assessor = functools.partial(risk_assessor_node, router=router)
    _graph_builder = functools.partial(graph_builder_node, neo4j_conn=neo4j_conn)
    _synthesizer = functools.partial(synthesizer_node, router=router)

    graph = StateGraph(ResearchState)

    graph.add_node("supervisor", _supervisor)
    graph.add_node("planner", _planner)
    graph.add_node("query_refiner", _query_refiner)
    graph.add_node("search_and_analyze", _search_and_analyze)
    graph.add_node("verifier", _verifier)
    graph.add_node("risk_assessor", _risk_assessor)
    graph.add_node("graph_builder", _graph_builder)
    graph.add_node("synthesizer", _synthesizer)

    # Entry: always start with supervisor
    graph.add_edge(START, "supervisor")

    # Supervisor routes to any sub-agent or END
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "planner": "planner",
            "query_refiner": "query_refiner",
            "search_and_analyze": "search_and_analyze",
            "verifier": "verifier",
            "risk_assessor": "risk_assessor",
            "graph_builder": "graph_builder",
            "synthesizer": "synthesizer",
            END: END,
        },
    )

    # Every sub-agent returns to supervisor after completion
    for node_name in [
        "planner",
        "query_refiner",
        "search_and_analyze",
        "verifier",
        "risk_assessor",
        "graph_builder",
        "synthesizer",
    ]:
        graph.add_edge(node_name, "supervisor")

    return graph


def compile_research_graph(
    settings: Settings,
    registry: LLMRegistry,
    neo4j_conn: Neo4jConnection,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the research graph, optionally with a checkpointer."""
    graph = build_research_graph(settings, registry, neo4j_conn)
    return graph.compile(checkpointer=checkpointer)
