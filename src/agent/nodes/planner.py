"""Planner node — generates a structured, phased research plan (Claude Sonnet 4.6)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.planner import PLANNER_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AuditEntry, ResearchPlan
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def planner_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Generate a phased research plan for the target."""
    writer = get_stream_writer()
    max_phases = state.get("max_phases", 5)
    writer({"node": "planner", "status": "started", "max_phases": max_phases})

    prompt = PLANNER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        objectives=", ".join(state.get("research_objectives", [])),
        max_phases=max_phases,
        max_phases_times_3=max_phases * 3,
    )

    start = time.monotonic()
    result = await router.invoke(
        "planner",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Generate the research plan now."),
        ],
        structured_output=ResearchPlan,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    usage = router.last_usage

    plan = result if isinstance(result, ResearchPlan) else ResearchPlan(phases=[])
    plan_dicts = [p.model_dump() for p in plan.phases]

    # Safety: if LLM generates more phases than requested, trim to max_phases.
    if len(plan_dicts) > max_phases:
        logger.warning(
            "planner_phase_count_trimmed",
            generated=len(plan_dicts),
            max_phases=max_phases,
        )
        plan_dicts = plan_dicts[:max_phases]

    audit = AuditEntry(
        node="planner",
        action="generate_plan",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="anthropic/claude-sonnet-4.6",
        output_summary=f"Generated {len(plan_dicts)}-phase plan with {plan.total_estimated_queries} queries",
        duration_ms=elapsed_ms,
        tokens_consumed=usage["tokens"],
        cost_usd=usage["cost"],
    )

    writer({"node": "planner", "status": "complete", "phases": len(plan_dicts)})

    return {
        "research_plan": plan_dicts,
        # Do NOT override max_phases — the value set by the API request is authoritative.
        # The planner is instructed to generate exactly max_phases phases.
        "current_phase": 1,
        "phase_complete": False,
        "current_phase_searched": False,
        "current_phase_verified": False,
        "current_phase_risk_assessed": False,
        "audit_log": [audit.model_dump()],
    }
