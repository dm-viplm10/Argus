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
    """Cross-reference extracted facts and assign final confidence scores."""
    writer = get_stream_writer()
    writer({"node": "verifier", "status": "started"})

    facts = state.get("extracted_facts", [])
    if not facts:
        writer({"node": "verifier", "status": "skipped", "reason": "no facts to verify"})
        return {}

    prompt = VERIFIER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        facts_json=json.dumps(facts, indent=2)[:50_000],
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
        model_used="anthropic/claude-sonnet-4.6",
        input_summary=f"Verified {len(facts)} extracted facts",
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
        "audit_log": [audit.model_dump()],
    }
