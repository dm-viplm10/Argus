"""Risk Assessor agent — identifies red flags and risk patterns (Claude Sonnet 4.6)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.base import StructuredOutputAgent
from src.agent.nodes.utils import truncate_json
from src.models.schemas import RiskAssessment
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Per-input context budget (chars of JSON) — keeps the risk assessor's total prompt
# within model token limits while preserving the most risk-relevant content.
_MAX_FLAGS_CHARS = 10_000       # existing flags provided as de-duplication context
_MAX_FINDINGS_CHARS = 40_000   # new verified facts to assess
_MAX_RELATIONSHIPS_CHARS = 20_000  # relationship graph for structural risk signals


class RiskAssessorAgent(StructuredOutputAgent):
    """Evaluates new verified findings for risk flags, avoiding duplicate flags from prior phases."""

    name = "risk_assessor"
    task = "risk_assessor"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Evaluate new verified findings for risk flags."""
        writer = get_stream_writer()
        writer({"node": "risk_assessor", "status": "started"})

        all_verified = state.get("verified_facts", [])
        already_assessed = state.get("risk_assessed_facts_count", 0)
        new_verified = all_verified[already_assessed:]

        if not new_verified:
            facts_verified_count = state.get("facts_verified_count", 0)
            if facts_verified_count > already_assessed:
                extracted = state.get("extracted_facts", [])
                new_verified = extracted[already_assessed:facts_verified_count]
                if new_verified:
                    writer({"node": "risk_assessor", "status": "fallback", "reason": "using extracted_facts (verified_facts empty)"})

        if not new_verified:
            writer({"node": "risk_assessor", "status": "skipped", "reason": "no new verified facts"})
            return {"current_phase_risk_assessed": True}

        existing_flags = state.get("risk_flags", [])
        relationships = state.get("relationships", [])

        existing_flags_summary = [
            {"flag": f.get("flag", ""), "category": f.get("category", ""), "severity": f.get("severity", "")}
            for f in existing_flags
        ]

        prompt = self._prompt_registry.get_prompt(
            "risk_assessor",
            target_name=state["target_name"],
            target_context=state.get("target_context", ""),
            existing_flags_json=truncate_json(existing_flags_summary, _MAX_FLAGS_CHARS) if existing_flags_summary else "None identified yet.",
            findings_json=truncate_json(new_verified, _MAX_FINDINGS_CHARS),
            relationships_json=truncate_json(relationships, _MAX_RELATIONSHIPS_CHARS),
        )

        result, elapsed_ms, usage = await self._invoke_structured(
            [
                SystemMessage(content=prompt),
                HumanMessage(content="Conduct your risk assessment now. Be thorough and unflinching."),
            ],
            RiskAssessment,
        )

        output = result if isinstance(result, RiskAssessment) else RiskAssessment()
        flags = [f.model_dump() for f in output.risk_flags]

        writer({
            "node": "risk_assessor",
            "status": "complete",
            "new_risk_flags": len(flags),
            "overall_score": output.overall_risk_score,
        })

        return {
            "risk_flags": flags,
            "overall_risk_score": output.overall_risk_score,
            "risk_assessed_facts_count": already_assessed + len(new_verified),
            "current_phase_risk_assessed": True,
            **self._build_audit(
                action="assess_risk",
                model_used=self._get_model_slug(),
                input_summary=f"Assessed {len(new_verified)} new verified facts ({already_assessed} already assessed), {len(existing_flags)} existing flags provided as context",
                output_summary=f"Identified {len(flags)} new risk flags, overall score: {output.overall_risk_score}",
                duration_ms=elapsed_ms,
                tokens_consumed=usage["tokens"],
                cost_usd=usage["cost"],
            ),
        }
