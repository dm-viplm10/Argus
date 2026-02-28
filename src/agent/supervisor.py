"""Supervisor node — orchestrates the research pipeline by routing to sub-agents."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.supervisor import SUPERVISOR_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AuditEntry, SupervisorDecision
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def supervisor_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Evaluate current state and decide which sub-agent to invoke next."""
    writer = get_stream_writer()
    iteration = state.get("iteration_count", 0) + 1
    writer({"node": "supervisor", "status": "deciding", "iteration": iteration})

    prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        target_name=state.get("target_name", ""),
        target_context=state.get("target_context", ""),
        objectives=", ".join(state.get("research_objectives", [])),
        current_phase=state.get("current_phase", 0),
        max_phases=state.get("max_phases", 5),
        facts_count=len(state.get("extracted_facts", [])),
        entities_count=len(state.get("entities", [])),
        verified_count=len(state.get("verified_facts", [])),
        risk_count=len(state.get("risk_flags", [])),
        graph_nodes_count=len(state.get("graph_nodes_created", [])),
        searches_count=len(state.get("search_queries_executed", [])),
        iteration_count=iteration,
        has_plan=bool(state.get("research_plan")),
        has_risk=bool(state.get("risk_flags")),
        has_report=bool(state.get("final_report")),
        phase_complete=state.get("phase_complete", False),
    )

    start = time.monotonic()
    result = await router.invoke(
        "supervisor",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Decide the next action."),
        ],
        structured_output=SupervisorDecision,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    decision = result if isinstance(result, SupervisorDecision) else SupervisorDecision(
        next_agent="FINISH", reasoning="Failed to parse decision"
    )

    logger.info(
        "supervisor_decision",
        next_agent=decision.next_agent,
        reasoning=decision.reasoning,
        iteration=iteration,
    )

    audit = AuditEntry(
        node="supervisor",
        action="route_decision",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="anthropic/claude-sonnet-4.6",
        output_summary=f"Routed to {decision.next_agent}: {decision.reasoning}",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "supervisor",
        "status": "routed",
        "next_agent": decision.next_agent,
        "reasoning": decision.reasoning,
    })

    updates: dict[str, Any] = {
        "current_agent": decision.next_agent,
        "next_action": decision.next_agent,
        "iteration_count": iteration,
        "audit_log": [audit.model_dump()],
    }

    # Handle phase advancement
    if decision.next_agent == "query_refiner" and state.get("phase_complete"):
        new_phase = state.get("current_phase", 1) + 1
        updates["current_phase"] = new_phase
        updates["phase_complete"] = False
        logger.info("phase_advanced", new_phase=new_phase)

    return updates
