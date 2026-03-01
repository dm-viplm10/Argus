"""LangGraph supervisor graph definition — wires all nodes together with routing."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.edges import route_from_supervisor
from src.agent.nodes import (
    GraphBuilderNode,
    PhaseStrategistAgent,
    PlannerAgent,
    QueryRefinerAgent,
    RiskAssessorAgent,
    SearchAndAnalyzeAgent,
    SupervisorAgent,
    SynthesizerAgent,
    VerifierAgent,
)
from src.agent.prompts.registry import PromptRegistry
from src.agent.state import ResearchState
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

    Uses agent instances with dependency injection. Each agent's run method
    satisfies LangGraph's node interface: async def(state) -> dict.
    """
    router = ModelRouter(registry)
    prompt_registry = PromptRegistry()

    agents = {
        "supervisor": SupervisorAgent(router=router, prompt_registry=prompt_registry),
        "planner": PlannerAgent(router=router, prompt_registry=prompt_registry),
        "phase_strategist": PhaseStrategistAgent(router=router, prompt_registry=prompt_registry),
        "query_refiner": QueryRefinerAgent(router=router, prompt_registry=prompt_registry),
        "search_and_analyze": SearchAndAnalyzeAgent(
            registry=registry,
            settings=settings,
            prompt_registry=prompt_registry,
        ),
        "verifier": VerifierAgent(
            registry=registry,
            settings=settings,
            prompt_registry=prompt_registry,
        ),
        "risk_assessor": RiskAssessorAgent(router=router, prompt_registry=prompt_registry),
        "graph_builder": GraphBuilderNode(neo4j_conn=neo4j_conn),
        "synthesizer": SynthesizerAgent(router=router, prompt_registry=prompt_registry),
    }

    graph = StateGraph(ResearchState)

    for name, agent in agents.items():
        graph.add_node(name, agent.run)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "planner": "planner",
            "phase_strategist": "phase_strategist",
            "query_refiner": "query_refiner",
            "search_and_analyze": "search_and_analyze",
            "verifier": "verifier",
            "risk_assessor": "risk_assessor",
            "graph_builder": "graph_builder",
            "synthesizer": "synthesizer",
            END: END,
        },
    )

    for node_name in [
        "planner",
        "phase_strategist",
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
