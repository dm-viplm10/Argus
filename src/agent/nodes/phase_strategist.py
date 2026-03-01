"""Phase Strategist agent — evaluates Phase 1 findings and dynamically decides next phases."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.base import StructuredOutputAgent
from src.models.llm_registry import MODEL_CONFIG
from src.models.schemas import AuditEntry, PhaseStrategyDecision
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _build_findings_summary(state: dict[str, Any]) -> str:
    """Build a concise summary of Phase 1 findings for the strategist."""
    parts: list[str] = []

    facts = state.get("extracted_facts", [])
    if facts:
        sample = facts[-15:]
        parts.append("### Extracted Facts")
        for f in sample:
            cat = f.get("category", "")
            conf = f.get("confidence", 0)
            parts.append(f"- [{cat}] {f.get('fact', '')} (confidence: {conf})")
        if len(facts) > 15:
            parts.append(f"... and {len(facts) - 15} more")

    entities = state.get("entities", [])
    if entities:
        parts.append("\n### Entities Discovered")
        for e in entities[-10:]:
            parts.append(f"- {e.get('name', '')} ({e.get('type', '')})")
        if len(entities) > 10:
            parts.append(f"... and {len(entities) - 10} more")

    verified = state.get("verified_facts", [])
    if verified:
        parts.append("\n### Verified Facts")
        for v in verified[-8:]:
            parts.append(f"- {v.get('fact', '')} (confidence: {v.get('final_confidence', 0)})")

    risk_flags = state.get("risk_flags", [])
    if risk_flags:
        parts.append("\n### Risk Flags")
        for r in risk_flags[-8:]:
            parts.append(f"- [{r.get('severity', '')}] {r.get('flag', '')} ({r.get('category', '')})")

    unverified = state.get("unverified_claims", [])
    if unverified:
        parts.append("\n### Unverified Claims (need follow-up)")
        for u in unverified[-5:]:
            parts.append(f"- {u}")

    if not parts:
        parts.append("No significant facts, entities, or risk flags extracted yet.")

    return "\n".join(parts)


class PhaseStrategistAgent(StructuredOutputAgent):
    """Evaluates Phase 1 findings and decides whether to add phases or proceed to synthesis."""

    name = "phase_strategist"
    task = "phase_strategist"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Evaluate Phase 1 findings and decide the next steps."""
        writer = get_stream_writer()
        writer({"node": "phase_strategist", "status": "started"})

        findings_summary = _build_findings_summary(state)

        prompt = self._prompt_registry.get_prompt(
            "phase_strategist",
            target_name=state.get("target_name", ""),
            target_context=state.get("target_context", ""),
            objectives=", ".join(state.get("research_objectives", [])),
            findings_summary=findings_summary,
        )

        start = time.monotonic()
        result = await self._router.invoke(
            "phase_strategist",
            [
                SystemMessage(content=prompt),
                HumanMessage(content="Evaluate the Phase 1 findings and decide the next steps."),
            ],
            structured_output=PhaseStrategyDecision,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        usage = self._router.last_usage

        decision = result if isinstance(result, PhaseStrategyDecision) else PhaseStrategyDecision(
            action="synthesize", phases_to_add=[], reasoning="Parse failure"
        )

        model_spec = MODEL_CONFIG.get("phase_strategist")
        model_slug = model_spec.slug if model_spec else "unknown"

        audit = AuditEntry(
            node="phase_strategist",
            action="phase_decision",
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used=model_slug,
            output_summary=f"{decision.action}: {decision.reasoning[:200]}",
            duration_ms=elapsed_ms,
            tokens_consumed=usage["tokens"],
            cost_usd=usage["cost"],
        )

        updates: dict[str, Any] = {
            "audit_log": [audit.model_dump()],
        }

        if decision.action == "add_phases" and decision.phases_to_add:
            plan = list(state.get("research_plan", []))
            base_num = len(plan) + 1
            new_phases = []
            for i, p in enumerate(decision.phases_to_add):
                phase_dict = p if isinstance(p, dict) else p.model_dump()
                phase_dict["phase_number"] = base_num + i
                new_phases.append(phase_dict)
            plan.extend(new_phases)
            updates["research_plan"] = plan
            updates["max_phases"] = len(plan)
            updates["current_phase"] = base_num
            updates["phase_complete"] = False
            updates["current_phase_searched"] = False
            updates["current_phase_verified"] = False
            updates["current_phase_risk_assessed"] = False
            updates["dynamic_phases"] = False
            first_phase = new_phases[0]
            updates["pending_queries"] = first_phase.get("queries", [])
            writer({"node": "phase_strategist", "status": "phases_added", "count": len(new_phases)})
            logger.info("phase_strategist_added_phases", count=len(new_phases), phases=[p["name"] for p in new_phases])
        else:
            writer({"node": "phase_strategist", "status": "synthesize", "reasoning": decision.reasoning[:100]})
            logger.info("phase_strategist_synthesize", reasoning=decision.reasoning[:150])

        return updates
