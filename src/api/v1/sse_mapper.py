"""Map raw LangGraph astream_events to frontend-friendly SSE (event_type, data) pairs.

This module is the only place in the codebase that knows the shape of both
LangGraph's internal event format and the client-facing SSE protocol. It has
no dependencies on business logic and is independently testable.
"""

from __future__ import annotations

from typing import Any

# Node names registered in the StateGraph — used to filter astream_events
# down to graph-level node transitions (ignoring internal sub-chains).
GRAPH_NODES: frozenset[str] = frozenset({
    "supervisor", "planner", "phase_strategist", "query_refiner", "search_and_analyze",
    "verifier", "risk_assessor", "graph_builder", "synthesizer",
})


def to_sse_event(raw: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map a LangGraph stream event to a (event_type, data) pair for the client.

    Returns None for events that should not be forwarded (filtered out).
    The caller is responsible for JSON-encoding ``data`` before sending.
    """
    kind = raw["event"]
    node = raw.get("metadata", {}).get("langgraph_node", "")

    if kind == "on_chain_start" and raw.get("name") in GRAPH_NODES:
        return ("node_start", {"node": raw["name"]})

    if kind == "on_chain_end" and raw.get("name") in GRAPH_NODES:
        output = raw.get("data", {}).get("output") or {}
        summary: dict[str, Any] = {"node": raw["name"]}
        if isinstance(output, dict):
            for key in ("extracted_facts", "entities", "verified_facts",
                        "risk_flags", "pending_queries"):
                val = output.get(key)
                if isinstance(val, list) and val:
                    summary[key] = len(val)
            if output.get("research_plan"):
                summary["phases"] = len(output["research_plan"])
            if output.get("final_report"):
                summary["has_report"] = True
            if output.get("overall_risk_score") is not None:
                summary["risk_score"] = output["overall_risk_score"]
        return ("node_end", summary)

    if kind == "on_chat_model_stream":
        chunk = raw.get("data", {}).get("chunk")
        if chunk is None:
            return None
        content = getattr(chunk, "content", "")
        # Claude returns content as a list of typed blocks (thinking / text / tool_use)
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "thinking":
                    t = block.get("thinking", "")
                    if t:
                        return ("thinking", {"node": node, "content": t})
                elif block.get("type") == "text":
                    t = block.get("text", "")
                    if t:
                        return ("token", {"node": node, "content": t})
            return None
        if isinstance(content, str) and content:
            return ("token", {"node": node, "content": content})
        return None

    if kind == "on_tool_start":
        tool_input = raw.get("data", {}).get("input")
        return ("tool_start", {
            "node": node,
            "tool": raw.get("name", ""),
            "input": str(tool_input)[:500] if tool_input else "",
        })

    if kind == "on_tool_end":
        output = raw.get("data", {}).get("output", "")
        return ("tool_end", {
            "node": node,
            "tool": raw.get("name", ""),
            "output": str(output)[:500],
        })

    return None
