"""Verifier ReAct agent — actively verifies claims via web search and cross-referencing.

Converts the former single-shot verifier into a ReAct agent that reasons about which
facts are worth verifying, searches the web for independent confirmation, and assigns
confidence scores based on what it actually finds — not just internal consistency.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from src.agent.prompts.verifier import VERIFIER_SYSTEM_PROMPT
from src.agent.tools.tavily_search import create_tavily_search_tool
from src.agent.tools.web_scrape import WebScrapeTool
from src.config import Settings
from src.models.llm_registry import MODEL_CONFIG, LLMRegistry
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

MAX_VERIFICATION_SEARCHES = 10


# ---------------------------------------------------------------------------
# submit_verification tool — structured output mechanism for the ReAct agent.
# The agent calls this once after completing all verification searches.
# The node extracts results from tool_call args, never from free-text.
# ---------------------------------------------------------------------------

class _VerificationSchema(BaseModel):
    verified_facts: list[dict] = Field(
        description=(
            "ALL facts with updated confidence scores after verification. Each must have keys: "
            "fact (str), category (str), final_confidence (float 0-1), "
            "verification_method (web_verified|cross_referenced|unverifiable|self_reported_only), "
            "supporting_sources (list[str] of URLs including any newly found), "
            "contradicting_sources (list[str] of URLs), "
            "notes (str explaining how confidence was determined)."
        )
    )
    unverified_claims: list[str] = Field(
        description="Claims that could not be corroborated by any independent source."
    )
    contradictions: list[dict] = Field(
        description=(
            "Pairs of facts that contradict each other. Each must have keys: "
            "claim_a (str), claim_b (str), source_a (str), source_b (str), "
            "resolution (str explaining which is more credible and why)."
        )
    )


@tool(args_schema=_VerificationSchema)
def submit_verification(
    verified_facts: list[dict],
    unverified_claims: list[str],
    contradictions: list[dict],
) -> str:
    """Submit your complete verification results.

    Call this tool ONCE after you have finished all verification searches
    and cross-referencing. Include ALL facts — not just the ones you searched for.
    """
    return (
        f"Verification recorded: {len(verified_facts)} facts assessed, "
        f"{len(unverified_claims)} unverified, {len(contradictions)} contradictions."
    )


def _build_verifier_agent(
    registry: LLMRegistry,
    settings: Settings,
    system_prompt: str,
):
    """Construct the ReAct verification agent with search, scrape, and submit tools."""
    model = registry.get_model("verifier")
    tavily_tool = create_tavily_search_tool(settings)
    scrape_tool = WebScrapeTool()

    return create_react_agent(
        model=model,
        tools=[tavily_tool, scrape_tool, submit_verification],
        prompt=SystemMessage(content=system_prompt),
    )


def _extract_verification(
    messages: list,
) -> tuple[list[dict], list[str], list[dict]]:
    """Pull verification results from the submit_verification tool call args.

    Reads the tool call arguments directly — never the agent's free-text
    final message — to prevent AI-summary contamination.
    """
    verified_facts: list[dict] = []
    unverified_claims: list[str] = []
    contradictions: list[dict] = []

    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if tc.get("name") == "submit_verification":
                args = tc.get("args", {})
                verified_facts = args.get("verified_facts", [])
                unverified_claims = args.get("unverified_claims", [])
                contradictions = args.get("contradictions", [])

    return verified_facts, unverified_claims, contradictions


async def verifier_node(
    state: dict[str, Any],
    *,
    registry: LLMRegistry,
    settings: Settings,
) -> dict[str, Any]:
    """Actively verify newly extracted facts via web search and cross-referencing."""
    writer = get_stream_writer()
    writer({"node": "verifier", "status": "started"})

    # Delta: only verify facts extracted since the last verifier run
    all_facts = state.get("extracted_facts", [])
    already_verified_count = state.get("facts_verified_count", 0)
    new_facts = all_facts[already_verified_count:]

    if not new_facts:
        writer({"node": "verifier", "status": "skipped", "reason": "no new facts to verify"})
        # Must set current_phase_verified=True so supervisor progresses; otherwise infinite loop
        return {"current_phase_verified": True}

    # Build the system prompt
    system_prompt = VERIFIER_SYSTEM_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        supervisor_instructions=state.get("supervisor_instructions", "No specific instructions."),
        max_searches=MAX_VERIFICATION_SEARCHES,
    )

    # Build the user prompt with the facts to verify
    facts_json = json.dumps(new_facts, indent=2)[:50_000]
    user_prompt = (
        f"Here are {len(new_facts)} newly extracted facts about {state['target_name']} "
        f"that need verification:\n\n{facts_json}\n\n"
        f"Reason about which claims are most important to verify independently. "
        f"Search the web for the ones that matter. Then call submit_verification "
        f"with your complete results."
    )

    agent = _build_verifier_agent(registry, settings, system_prompt)

    start = time.monotonic()
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_prompt)]})
    elapsed_ms = int((time.monotonic() - start) * 1000)

    messages = result.get("messages", [])
    verified, unverified_claims, contradictions = _extract_verification(messages)

    if not verified and not unverified_claims:
        logger.warning(
            "verifier_no_results",
            facts_count=len(new_facts),
            reason="submit_verification not called or returned empty",
        )

    # Resolve model slug for audit
    model_spec = MODEL_CONFIG.get("verifier")
    model_slug = model_spec.slug if model_spec else "unknown"

    audit = AuditEntry(
        node="verifier",
        action="active_verification",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used=model_slug,
        input_summary=f"Verified {len(new_facts)} new facts (skipped {already_verified_count} already verified)",
        output_summary=(
            f"{len(verified)} verified, {len(unverified_claims)} unverified, "
            f"{len(contradictions)} contradictions"
        ),
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "verifier",
        "status": "complete",
        "verified": len(verified),
        "unverified": len(unverified_claims),
        "contradictions": len(contradictions),
    })

    return {
        "verified_facts": verified,
        "unverified_claims": unverified_claims,
        "contradictions": contradictions,
        # Advance the cursor so the next verifier call skips already-processed facts
        "facts_verified_count": already_verified_count + len(new_facts),
        # Signal to supervisor that verification is done for this phase
        "current_phase_verified": True,
        "audit_log": [audit.model_dump()],
    }
