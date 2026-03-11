"""Supervisor agent — orchestrates the research pipeline by routing to sub-agents."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.base import StructuredOutputAgent
from src.agent.cancellation import clear, is_cancelled
from src.agent.nodes.utils import reset_phase_flags
from src.models.schemas import SupervisorDecision
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SupervisorAgent(StructuredOutputAgent):
    """Evaluates current state and decides which sub-agent to invoke next."""

    name = "supervisor"
    task = "supervisor"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Evaluate current state and decide which sub-agent to invoke next."""
        writer = get_stream_writer()
        iteration = state.get("iteration_count", 0) + 1
        writer({"node": "supervisor", "status": "deciding", "iteration": iteration})

        research_id = state.get("research_id", "")
        if research_id and is_cancelled(research_id):
            logger.info("supervisor_cancelled", research_id=research_id, iteration=iteration)
            clear(research_id)
            writer({"node": "supervisor", "status": "cancelled"})
            return {
                "current_agent": "FINISH",
                "next_action": "FINISH",
                "supervisor_instructions": "",
                "iteration_count": iteration,
                **self._build_audit(action="cancelled", output_summary="Job cancelled by user"),
            }

        prompt = self._prompt_registry.get_prompt(
            "supervisor",
            target_name=state.get("target_name", ""),
            target_context=state.get("target_context", ""),
            objectives=", ".join(state.get("research_objectives", [])),
            current_phase=state.get("current_phase", 0),
            max_phases=state.get("max_phases", 5),
            dynamic_phases=state.get("dynamic_phases", False),
            phase_searched=state.get("current_phase_searched", False),
            phase_verified=state.get("current_phase_verified", False),
            phase_risk_assessed=state.get("current_phase_risk_assessed", False),
            phase_complete=state.get("phase_complete", False),
            facts_count=len(state.get("extracted_facts", [])),
            entities_count=len(state.get("entities", [])),
            verified_count=len(state.get("verified_facts", [])),
            risk_count=len(state.get("risk_flags", [])),
            graph_nodes_count=len(state.get("graph_nodes_created", [])),
            searches_count=len(state.get("search_queries_executed", [])),
            pending_queries_count=len(state.get("pending_queries", [])),
            iteration_count=iteration,
            has_plan=bool(state.get("research_plan")),
            has_report=bool(state.get("final_report")),
        )

        result, elapsed_ms, usage = await self._invoke_structured(
            [
                SystemMessage(content=prompt),
                HumanMessage(content="Decide the next action."),
            ],
            SupervisorDecision,
        )

        decision = result if isinstance(result, SupervisorDecision) else SupervisorDecision(
            next_agent="FINISH", reasoning="Failed to parse decision"
        )

        logger.info(
            "supervisor_decision",
            next_agent=decision.next_agent,
            reasoning=decision.reasoning,
            iteration=iteration,
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
            "supervisor_instructions": decision.instructions_for_agent,
            "iteration_count": iteration,
            **self._build_audit(
                action="route_decision",
                model_used=self._get_model_slug(),
                output_summary=f"Routed to {decision.next_agent}: {decision.reasoning}",
                duration_ms=elapsed_ms,
                tokens_consumed=usage["tokens"],
                cost_usd=usage["cost"],
            ),
        }

        if decision.next_agent == "query_refiner" and state.get("phase_complete"):
            new_phase = state.get("current_phase", 1) + 1
            updates.update(reset_phase_flags(new_phase=new_phase))
            logger.info("phase_advanced", new_phase=new_phase)

        return updates
