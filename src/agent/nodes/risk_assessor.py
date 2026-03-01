"""Risk Assessor node — identifies red flags and risk patterns (Claude Sonnet 4.6)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from src.agent.prompts.risk_assessor import RISK_ASSESSOR_SYSTEM_PROMPT
from src.models.model_router import ModelRouter
from src.models.schemas import AuditEntry, RiskAssessment
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def risk_assessor_node(state: dict[str, Any], *, router: ModelRouter) -> dict[str, Any]:
    """Evaluate new verified findings for risk flags, avoiding duplicate flags from prior phases."""
    writer = get_stream_writer()
    writer({"node": "risk_assessor", "status": "started"})

    # Delta: only assess verified facts added since the last risk_assessor run
    all_verified = state.get("verified_facts", [])
    already_assessed = state.get("risk_assessed_facts_count", 0)
    new_verified = all_verified[already_assessed:]

    # Fallback: when verifier didn't populate verified_facts (e.g. ReAct agent missed tool call)
    # but did advance facts_verified_count, use extracted_facts for risk assessment
    if not new_verified:
        facts_verified_count = state.get("facts_verified_count", 0)
        if facts_verified_count > already_assessed:
            extracted = state.get("extracted_facts", [])
            new_verified = extracted[already_assessed:facts_verified_count]
            if new_verified:
                writer({"node": "risk_assessor", "status": "fallback", "reason": "using extracted_facts (verified_facts empty)"})

    if not new_verified:
        writer({"node": "risk_assessor", "status": "skipped", "reason": "no new verified facts"})
        # Still mark phase as risk-assessed to prevent infinite supervisor→risk_assessor loop
        return {"current_phase_risk_assessed": True}

    existing_flags = state.get("risk_flags", [])
    relationships = state.get("relationships", [])

    # Summarise existing flags for the prompt to prevent re-flagging
    existing_flags_summary = [
        {"flag": f.get("flag", ""), "category": f.get("category", ""), "severity": f.get("severity", "")}
        for f in existing_flags
    ]

    prompt = RISK_ASSESSOR_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        existing_flags_json=json.dumps(existing_flags_summary, indent=2)[:10_000] if existing_flags_summary else "None identified yet.",
        findings_json=json.dumps(new_verified, indent=2)[:40_000],
        relationships_json=json.dumps(relationships, indent=2)[:20_000],
    )

    start = time.monotonic()
    result = await router.invoke(
        "risk_assessor",
        [
            SystemMessage(content=prompt),
            HumanMessage(content="Conduct your risk assessment now. Be thorough and unflinching."),
        ],
        structured_output=RiskAssessment,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    usage = router.last_usage

    output = result if isinstance(result, RiskAssessment) else RiskAssessment()
    flags = [f.model_dump() for f in output.risk_flags]

    audit = AuditEntry(
        node="risk_assessor",
        action="assess_risk",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="openai/gpt-4.1",
        input_summary=f"Assessed {len(new_verified)} new verified facts ({already_assessed} already assessed), {len(existing_flags)} existing flags provided as context",
        output_summary=f"Identified {len(flags)} new risk flags, overall score: {output.overall_risk_score}",
        duration_ms=elapsed_ms,
        tokens_consumed=usage["tokens"],
        cost_usd=usage["cost"],
    )

    writer({
        "node": "risk_assessor",
        "status": "complete",
        "new_risk_flags": len(flags),
        "overall_score": output.overall_risk_score,
    })

    return {
        "risk_flags": flags,
        "overall_risk_score": output.overall_risk_score,
        # Advance cursor so the next call only sees facts from subsequent phases
        "risk_assessed_facts_count": already_assessed + len(new_verified),
        # Signal to supervisor that risk assessment is done for this phase
        "current_phase_risk_assessed": True,
        "audit_log": [audit.model_dump()],
    }
