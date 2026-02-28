"""Verifier node — cross-references facts and assigns confidence scores (Claude Sonnet 4.6)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.verifier import VERIFIER_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AuditEntry, VerifierOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def verifier_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Cross-reference only newly extracted facts and assign confidence scores."""
    writer = get_stream_writer()
    writer({"node": "verifier", "status": "started"})

    # Delta: only verify facts extracted since the last verifier run
    all_facts = state.get("extracted_facts", [])
    already_verified_count = state.get("facts_verified_count", 0)
    new_facts = all_facts[already_verified_count:]

    if not new_facts:
        writer({"node": "verifier", "status": "skipped", "reason": "no new facts to verify"})
        return {}

    prompt = VERIFIER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        supervisor_instructions=state.get("supervisor_instructions", "No specific instructions."),
        facts_json=json.dumps(new_facts, indent=2)[:50_000],
    )

    start = time.monotonic()
    result = await router.invoke(
        "verifier",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Cross-reference and verify all facts now."),
        ],
        structured_output=VerifierOutput,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    output = result if isinstance(result, VerifierOutput) else VerifierOutput()

    verified = [v.model_dump() for v in output.verified_facts]
    contradictions = [c.model_dump() for c in output.contradictions]

    audit = AuditEntry(
        node="verifier",
        action="cross_reference",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="google/gemini-2.5-pro",
        input_summary=f"Verified {len(new_facts)} new facts (skipped {already_verified_count} already verified)",
        output_summary=f"{len(verified)} verified, {len(output.unverified_claims)} unverified, {len(contradictions)} contradictions",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "verifier",
        "status": "complete",
        "verified": len(verified),
        "unverified": len(output.unverified_claims),
        "contradictions": len(contradictions),
    })

    return {
        "verified_facts": verified,
        "unverified_claims": output.unverified_claims,
        "contradictions": contradictions,
        # Advance the cursor so the next verifier call skips already-processed facts
        "facts_verified_count": already_verified_count + len(new_facts),
        "audit_log": [audit.model_dump()],
    }
