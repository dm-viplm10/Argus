"""Synthesizer agent — generates the final comprehensive research report (Claude Sonnet 4.6)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.base import StructuredOutputAgent
from src.agent.nodes.utils import truncate_json
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Per-input context budget (chars of JSON). Tuned so the total prompt fits within
# the synthesizer's context window while preserving the most actionable data.
_MAX_VERIFIED_FACTS_CHARS = 30_000
_MAX_ENTITIES_CHARS = 15_000
_MAX_RISK_CHARS = 15_000
_MAX_UNVERIFIED_CHARS = 10_000


class SynthesizerAgent(StructuredOutputAgent):
    """Generates the final comprehensive Markdown research report."""

    name = "synthesizer"
    task = "synthesizer"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Generate the final comprehensive Markdown research report."""
        writer = get_stream_writer()
        writer({"node": "synthesizer", "status": "started"})

        prompt = self._prompt_registry.get_prompt(
            "synthesizer",
            target_name=state["target_name"],
            target_context=state.get("target_context", ""),
            verified_facts_json=truncate_json(state.get("verified_facts", []), _MAX_VERIFIED_FACTS_CHARS),
            entities_json=truncate_json(state.get("entities", []), _MAX_ENTITIES_CHARS),
            risk_json=truncate_json(state.get("risk_flags", []), _MAX_RISK_CHARS),
            unverified_json=truncate_json(state.get("unverified_claims", []), _MAX_UNVERIFIED_CHARS),
            searches_count=len(state.get("search_queries_executed", [])),
            sources_count=len(state.get("urls_visited", set())),
            phases_completed=state.get("current_phase", 0),
        )

        result, elapsed_ms, usage = await self._invoke_structured(
            [
                SystemMessage(content=prompt),
                HumanMessage(content="Write the complete investigation report now."),
            ],
        )

        report = getattr(result, "content", str(result))

        writer({"node": "synthesizer", "status": "complete", "report_length": len(report)})

        return {
            "final_report": report,
            **self._build_audit(
                action="generate_report",
                model_used=self._get_model_slug(),
                output_summary=f"Generated report with {len(report)} characters",
                duration_ms=elapsed_ms,
                tokens_consumed=usage["tokens"],
                cost_usd=usage["cost"],
            ),
        }
