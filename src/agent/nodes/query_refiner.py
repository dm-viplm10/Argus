"""Query Refiner agent — generates search queries based on current phase and prior findings."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.base import StructuredOutputAgent
from src.models.schemas import AuditEntry, RefinedQueries
from src.utils.logging import get_logger

logger = get_logger(__name__)


class QueryRefinerAgent(StructuredOutputAgent):
    """Generates refined search queries for the current phase."""

    name = "query_refiner"
    task = "query_refiner"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Generate refined search queries for the current phase."""
        writer = get_stream_writer()
        current_phase = state.get("current_phase", 1)
        writer({"node": "query_refiner", "status": "started", "phase": current_phase})

        plan = state.get("research_plan", [])
        phase_idx = current_phase - 1
        phase_info = plan[phase_idx] if phase_idx < len(plan) else {}

        executed = [q.get("query", "") for q in state.get("search_queries_executed", [])]

        facts_summary = ""
        facts = state.get("extracted_facts", [])
        if facts:
            top_facts = facts[-10:]
            facts_summary = "\n".join(f"- {f.get('fact', '')}" for f in top_facts)

        prompt = self._prompt_registry.get_prompt(
            "query_refiner",
            target_name=state["target_name"],
            target_context=state.get("target_context", ""),
            phase_number=phase_info.get("phase_number", current_phase),
            phase_name=phase_info.get("name", f"Phase {current_phase}"),
            phase_description=phase_info.get("description", ""),
            predefined_queries=json.dumps(phase_info.get("queries", [])),
            findings_summary=facts_summary or "No findings yet.",
            executed_queries=json.dumps(executed[-20:]),
        )

        start = time.monotonic()
        result = await self._router.invoke(
            "query_refiner",
            [
                SystemMessage(content="You are a search query generation specialist."),
                HumanMessage(content=prompt),
            ],
            structured_output=RefinedQueries,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        refined = result if isinstance(result, RefinedQueries) else RefinedQueries(queries=[])
        new_queries = [q for q in refined.queries if q not in executed]

        audit = AuditEntry(
            node="query_refiner",
            action="generate_queries",
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used="openai/gpt-4.1-mini",
            output_summary=f"Generated {len(new_queries)} new queries for phase {current_phase}",
            duration_ms=elapsed_ms,
        )

        writer({"node": "query_refiner", "status": "complete", "queries_generated": len(new_queries)})

        return {
            "pending_queries": new_queries,
            "audit_log": [audit.model_dump()],
        }
