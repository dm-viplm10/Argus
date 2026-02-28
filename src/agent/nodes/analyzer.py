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
    """Extract structured facts, entities, and relationships from search results."""
    writer = get_stream_writer()
    writer({"node": "analyzer", "status": "started"})

    search_results = state.get("search_results", [])
    scraped_content = state.get("scraped_content", [])

    content_blocks: list[str] = []
    for sr in search_results:
        c = sr.get("content", "")
        if c:
            content_blocks.append(c)
    for sc in scraped_content:
        c = sc.get("content", "")
        if c:
            content_blocks.append(c)

    if not content_blocks:
        writer({"node": "analyzer", "status": "skipped", "reason": "no content to analyze"})
        return {}

    combined = "\n\n---\n\n".join(content_blocks)
    combined = truncate_content(combined, max_chars=100_000)

    prompt = ANALYZER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
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
        "audit_log": [audit.model_dump()],
    }
