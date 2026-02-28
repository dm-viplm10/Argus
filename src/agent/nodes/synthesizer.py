"""Synthesizer node — generates the final comprehensive research report (Claude Sonnet 4.6)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.synthesizer import SYNTHESIZER_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def synthesizer_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Generate the final comprehensive Markdown research report."""
    writer = get_stream_writer()
    writer({"node": "synthesizer", "status": "started"})

    prompt = SYNTHESIZER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        verified_facts_json=json.dumps(state.get("verified_facts", []), indent=2)[:30_000],
        entities_json=json.dumps(state.get("entities", []), indent=2)[:15_000],
        risk_json=json.dumps(state.get("risk_flags", []), indent=2)[:15_000],
        unverified_json=json.dumps(state.get("unverified_claims", []), indent=2)[:10_000],
        searches_count=len(state.get("search_queries_executed", [])),
        sources_count=len(state.get("urls_visited", set())),
        phases_completed=state.get("current_phase", 0),
    )

    start = time.monotonic()
    result = await router.invoke(
        "synthesizer",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Write the complete investigation report now."),
        ],
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    report = getattr(result, "content", str(result))

    audit = AuditEntry(
        node="synthesizer",
        action="generate_report",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="anthropic/claude-sonnet-4.6",
        output_summary=f"Generated report with {len(report)} characters",
        duration_ms=elapsed_ms,
    )

    writer({"node": "synthesizer", "status": "complete", "report_length": len(report)})

    return {
        "final_report": report,
        "audit_log": [audit.model_dump()],
    }
