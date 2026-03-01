"""Verifier ReAct agent — actively verifies claims via web search and cross-referencing."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from src.agent.base import ReActAgent
from src.agent.tools.tavily_search import create_tavily_search_tool
from src.agent.tools.web_scrape import WebScrapeTool
from src.models.llm_registry import MODEL_CONFIG
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

MAX_VERIFICATION_SEARCHES = 10
# Cap ReAct tool-call rounds so the verifier cannot loop search→scrape→submit indefinitely.
VERIFIER_RECURSION_LIMIT = 28


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
    and cross-referencing. Do NOT call this after each search or scrape —
    run all tavily_search and web_scrape calls first, then submit everything
    in a single call. Include ALL facts — not just the ones you searched for.
    After calling this tool, do not perform any more searches or scrapes."""
    return (
        f"Verification recorded: {len(verified_facts)} facts assessed, "
        f"{len(unverified_claims)} unverified, {len(contradictions)} contradictions."
    )


def _extract_verification(
    messages: list,
) -> tuple[list[dict], list[str], list[dict]]:
    """Pull verification results from the submit_verification tool call args."""
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


class VerifierAgent(ReActAgent):
    """Actively verify newly extracted facts via web search and cross-referencing."""

    name = "verifier"
    task = "verifier"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Actively verify newly extracted facts."""
        writer = get_stream_writer()
        writer({"node": "verifier", "status": "started"})

        all_facts = state.get("extracted_facts", [])
        already_verified_count = state.get("facts_verified_count", 0)
        new_facts = all_facts[already_verified_count:]

        if not new_facts:
            writer({"node": "verifier", "status": "skipped", "reason": "no new facts to verify"})
            return {"current_phase_verified": True}

        system_prompt = self._prompt_registry.get_prompt(
            "verifier",
            target_name=state["target_name"],
            target_context=state.get("target_context", ""),
            supervisor_instructions=state.get("supervisor_instructions", "No specific instructions."),
            max_searches=MAX_VERIFICATION_SEARCHES,
        )

        facts_json = json.dumps(new_facts, indent=2)[:50_000]
        user_prompt = (
            f"Here are {len(new_facts)} newly extracted facts about {state['target_name']} "
            f"that need verification:\n\n{facts_json}\n\n"
            f"1) Reason about which claims are most important to verify independently. "
            f"2) Run tavily_search and web_scrape for the ones that matter — do multiple searches/scrapes as needed. "
            f"3) When you have gathered enough evidence (or reached the search budget), call submit_verification ONCE with your complete results. "
            f"4) After calling submit_verification, do NOT perform any more searches or scrapes. Your final tool call must be submit_verification — call it only once, at the very end."
        )

        model = self._registry.get_model("verifier")
        tavily_tool = create_tavily_search_tool(self._settings)
        scrape_tool = WebScrapeTool()

        agent = create_react_agent(
            model=model,
            tools=[tavily_tool, scrape_tool, submit_verification],
            prompt=SystemMessage(content=system_prompt),
        )

        start = time.monotonic()
        config = {"recursion_limit": VERIFIER_RECURSION_LIMIT}
        try:
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_prompt)]},
                config=config,
            )
        except GraphRecursionError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "verifier_recursion_limit_hit",
                limit=VERIFIER_RECURSION_LIMIT,
                elapsed_ms=elapsed_ms,
            )
            writer({"node": "verifier", "status": "recursion_limit", "message": "Stopped after max steps"})
            model_spec = MODEL_CONFIG.get("verifier")
            model_slug = model_spec.slug if model_spec else "unknown"
            return {
                "verified_facts": [],
                "unverified_claims": [f.get("fact", "") for f in new_facts],
                "contradictions": [],
                "facts_verified_count": already_verified_count + len(new_facts),
                "current_phase_verified": True,
                "audit_log": [
                    AuditEntry(
                        node="verifier",
                        action="active_verification",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        model_used=model_slug,
                        input_summary=f"Verified {len(new_facts)} new facts (recursion limit hit)",
                        output_summary="Stopped at recursion limit; no verification submitted",
                        duration_ms=elapsed_ms,
                    ).model_dump()
                ],
            }

        elapsed_ms = int((time.monotonic() - start) * 1000)

        messages = result.get("messages", [])
        verified, unverified_claims, contradictions = _extract_verification(messages)

        # If the agent stopped without calling submit_verification, force one more round
        # with only the submit tool so results are always recorded.
        if not verified and not unverified_claims:
            logger.warning(
                "verifier_no_submit",
                facts_count=len(new_facts),
                message="Agent did not call submit_verification; forcing submit-only round",
            )
            writer({"node": "verifier", "status": "forcing_submit"})
            submit_only_agent = create_react_agent(
                model=model,
                tools=[submit_verification],
                prompt=SystemMessage(
                    content="You must call the submit_verification tool with your complete verification results. "
                    "Include every fact from the conversation in either verified_facts or unverified_claims. "
                    "Do not output a text summary only — you must call the tool."
                ),
            )
            force_result = await submit_only_agent.ainvoke(
                {
                    "messages": messages
                    + [
                        HumanMessage(
                            content="You did not call submit_verification. You MUST call the submit_verification tool now with your complete assessment of all facts discussed above. "
                            "Include every fact in either verified_facts or unverified_claims. This is required."
                        )
                    ]
                },
                config={"recursion_limit": 5},
            )
            messages = force_result.get("messages", [])
            verified, unverified_claims, contradictions = _extract_verification(messages)

        if not verified and not unverified_claims:
            logger.warning(
                "verifier_no_results",
                facts_count=len(new_facts),
                reason="submit_verification not called or returned empty; treating all as unverified",
            )
            unverified_claims = [f.get("fact", "") for f in new_facts if f.get("fact")]

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
            "facts_verified_count": already_verified_count + len(new_facts),
            "current_phase_verified": True,
            "audit_log": [audit.model_dump()],
        }
