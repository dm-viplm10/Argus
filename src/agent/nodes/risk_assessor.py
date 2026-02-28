"""Risk Assessor node — identifies red flags and risk patterns (Grok 3)."""

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
    """Evaluate all findings for risk flags using Grok 3's unfiltered analysis."""
    writer = get_stream_writer()
    writer({"node": "risk_assessor", "status": "started"})

    verified_facts = state.get("verified_facts", [])
    relationships = state.get("relationships", [])

    if not verified_facts:
        writer({"node": "risk_assessor", "status": "skipped", "reason": "no verified facts"})
        return {}

    prompt = RISK_ASSESSOR_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        findings_json=json.dumps(verified_facts, indent=2)[:40_000],
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

    output = result if isinstance(result, RiskAssessment) else RiskAssessment()
    flags = [f.model_dump() for f in output.risk_flags]

    audit = AuditEntry(
        node="risk_assessor",
        action="assess_risk",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="x-ai/grok-3",
        input_summary=f"Assessed {len(verified_facts)} verified facts and {len(relationships)} relationships",
        output_summary=f"Identified {len(flags)} risk flags, overall score: {output.overall_risk_score}",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "risk_assessor",
        "status": "complete",
        "risk_flags": len(flags),
        "overall_score": output.overall_risk_score,
    })

    return {
        "risk_flags": flags,
        "overall_risk_score": output.overall_risk_score,
        "audit_log": [audit.model_dump()],
    }
