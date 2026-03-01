"""Conditional edge and routing logic for the supervisor graph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END


def route_from_supervisor(state: dict[str, Any]) -> str:
    """Route from supervisor to the next sub-agent or END.

    Reads state['next_action'] set by the supervisor node and maps it
    to a graph node name or END.
    """
    action = state.get("next_action", "FINISH")

    valid_agents = {
        "planner",
        "query_refiner",
        "search_and_analyze",
        "verifier",
        "risk_assessor",
        "graph_builder",
        "synthesizer",
    }

    if action in valid_agents:
        return action

    return END
