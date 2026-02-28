"""Analyzer node — extracts facts, entities, and relationships from content (Gemini 2.5 Pro)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.analyzer import ANALYZER_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AnalyzerOutput, AuditEntry
from src.utils.logging import get_logger
from src.utils.text_processing import truncate_content

logger = get_logger(__name__)


async def analyzer_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Extract structured facts, entities, and relationships from new search results only."""
    writer = get_stream_writer()
    current_phase = state.get("current_phase", 1)
    writer({"node": "analyzer", "status": "started", "phase": current_phase})

    # Delta: only process results added since the last analyzer run
    all_results = state.get("search_results", [])
    already_analyzed = state.get("search_results_analyzed_count", 0)
    new_results = all_results[already_analyzed:]

    # Scraped content is always targeted at the current phase — process all of it
    scraped_content = state.get("scraped_content", [])

    content_blocks: list[str] = []
    for sr in new_results:
        c = sr.get("content", "")
        if c:
            content_blocks.append(c)
    for sc in scraped_content:
        c = sc.get("content", "")
        if c:
            content_blocks.append(c)

    if not content_blocks:
        writer({"node": "analyzer", "status": "skipped", "reason": "no new content to analyze"})
        return {}

    combined = "\n\n---\n\n".join(content_blocks)
    combined = truncate_content(combined, max_chars=100_000)

    # Pull phase info from the research plan for context
    plan = state.get("research_plan", [])
    phase_idx = current_phase - 1
    phase_info = plan[phase_idx] if phase_idx < len(plan) else {}

    prompt = ANALYZER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        phase_number=phase_info.get("phase_number", current_phase),
        phase_name=phase_info.get("name", f"Phase {current_phase}"),
        phase_description=phase_info.get("description", ""),
        expected_info_types=", ".join(phase_info.get("expected_info_types", [])),
        supervisor_instructions=state.get("supervisor_instructions", "No specific instructions."),
        content=combined,
    )

    start = time.monotonic()
    result = await router.invoke(
        "analyzer",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Extract all facts, entities, and relationships now."),
        ],
        structured_output=AnalyzerOutput,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    output = result if isinstance(result, AnalyzerOutput) else AnalyzerOutput()

    facts = [f.model_dump() for f in output.facts]
    entities = [e.model_dump() for e in output.entities]
    relationships = [r.model_dump() for r in output.relationships]

    audit = AuditEntry(
        node="analyzer",
        action="extract_entities",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="google/gemini-2.5-pro",
        input_summary=f"Processed {len(content_blocks)} content blocks",
        output_summary=f"Extracted {len(facts)} facts, {len(entities)} entities, {len(relationships)} relationships",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "analyzer",
        "status": "complete",
        "facts": len(facts),
        "entities": len(entities),
        "relationships": len(relationships),
    })

    return {
        "extracted_facts": facts,
        "entities": entities,
        "relationships": relationships,
        # Advance the cursor so the next analyzer call skips already-processed results
        "search_results_analyzed_count": already_analyzed + len(new_results),
        "audit_log": [audit.model_dump()],
    }
