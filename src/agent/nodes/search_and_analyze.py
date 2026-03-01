"""Search & Analyze ReAct agent — executes searches, scrapes, and extracts structured findings.

Merges the former search_agent + analyzer nodes into a single ReAct loop. The agent
uses Tavily search and web_scrape to gather content, then calls submit_findings once
to emit structured facts, entities, and relationships directly — eliminating the
intermediate state hop and the AI-summary contamination of the old two-node design.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from src.agent.tools.tavily_search import create_tavily_search_tool
from src.agent.tools.web_scrape import WebScrapeTool
from src.config import Settings
from src.models.llm_registry import LLMRegistry
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

MAX_QUERIES_PER_BATCH = 6


# ---------------------------------------------------------------------------
# submit_findings tool — the structured output mechanism for the ReAct agent.
# The agent calls this once after gathering and analyzing all content.
# The node extracts the findings from the tool_call args, never from the
# agent's free-text final message (which was the old contamination vector).
# ---------------------------------------------------------------------------

class _FindingsSchema(BaseModel):
    facts: list[dict] = Field(
        description=(
            "List of extracted facts. Each fact must have keys: "
            "fact (str), category (biographical|professional|financial|legal|social|behavioral), "
            "confidence (float 0-1), source_url (str), source_type (official|news|social|forum|filing|unknown), "
            "date_mentioned (YYYY-MM-DD or null), entities_involved (list[str])."
        )
    )
    entities: list[dict] = Field(
        description=(
            "List of extracted entities. Each entity must have keys: "
            "name (str), type (person|organization|fund|location|event|document), "
            "attributes (dict with any of: role, title, position, location, founded, url, industry, description, date, value), "
            "sources (list[str] of URLs)."
        )
    )
    relationships: list[dict] = Field(
        description=(
            "List of relationships between entities. Each must have keys: "
            "source_entity (str), target_entity (str), "
            "relationship_type (WORKS_AT|OWNS|BOARD_MEMBER_OF|ASSOCIATED_WITH|LITIGATED|MANAGES|INVESTED_IN|LOCATED_IN|MENTIONED_IN), "
            "evidence (str), confidence (float 0-1), source_url (str)."
        )
    )


@tool(args_schema=_FindingsSchema)
def submit_findings(
    facts: list[dict],
    entities: list[dict],
    relationships: list[dict],
) -> str:
    """Submit your complete structured research findings.

    Call this tool ONCE after you have finished all searches and scraping.
    Do NOT call this mid-research — gather all available information first,
    then submit everything in a single call.
    """
    return (
        f"Findings recorded: {len(facts)} facts, "
        f"{len(entities)} entities, {len(relationships)} relationships."
    )


SEARCH_ANALYZE_SYSTEM = """\
You are an expert web researcher and intelligence analyst conducting OSINT investigation.
Your job is to execute search queries, scrape high-value sources, analyze all content,
and submit structured findings in one pass.

## Workflow

1. Execute EVERY query using the tavily_search tool.
2. Review the results — identify URLs with the highest-quality information
   (official sources, news articles, regulatory filings, professional profiles).
3. For important URLs where the snippet is insufficient, use web_scrape to get full content.
   Avoid scraping the same URL twice.
4. As you gather content, build up your understanding of facts, entities, and relationships
   relevant to the target.
5. Once ALL queries are executed and high-value URLs are scraped, call submit_findings
   with your complete structured analysis.

## Extraction Guidelines

**Facts** — specific, verifiable claims. Assign confidence based on source quality:
- Official filings, government records: 0.85–0.95
- Major news outlets: 0.70–0.85
- Industry publications: 0.60–0.75
- Personal websites, LinkedIn: 0.40–0.60
- Forums, social media: 0.20–0.40

**Entities** — every person, organization, fund, location, event, or document mentioned
in connection with the target. Completeness matters for network mapping.

**Relationships** — connections between entities with supporting evidence.

## Rules
- NEVER fabricate facts not present in the content.
- NEVER assign confidence > 0.5 to single-source unverified claims.
- NEVER skip entities even if they seem minor.
- If a page is irrelevant to the target, still note the null result and move on.
- Call submit_findings exactly ONCE at the very end.

## Phase Context

{phase_context}

## Supervisor Instructions

{supervisor_instructions}
"""


def _build_agent(registry: LLMRegistry, settings: Settings, phase_context: str, supervisor_instructions: str):
    """Construct the ReAct agent with all three tools bound for this invocation."""
    model = registry.get_model("search_and_analyze")
    tavily_tool = create_tavily_search_tool(settings)
    scrape_tool = WebScrapeTool()

    system_prompt = SEARCH_ANALYZE_SYSTEM.format(
        phase_context=phase_context,
        supervisor_instructions=supervisor_instructions or "No specific instructions.",
    )

    return create_react_agent(
        model=model,
        tools=[tavily_tool, scrape_tool, submit_findings],
        prompt=SystemMessage(content=system_prompt),
    )


def _extract_findings(messages: list) -> tuple[list[dict], list[dict], list[dict], set[str]]:
    """Pull structured findings and visited URLs directly from tool_call args.

    Reads the submit_findings tool call args — never the agent's free-text
    final message. Also collects URLs from web_scrape tool calls for dedup.
    """
    facts: list[dict] = []
    entities: list[dict] = []
    relationships: list[dict] = []
    urls_visited: set[str] = set()

    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            if name == "submit_findings":
                facts = args.get("facts", [])
                entities = args.get("entities", [])
                relationships = args.get("relationships", [])
            elif name == "web_scrape":
                url = args.get("url", "") if isinstance(args, dict) else ""
                if url:
                    urls_visited.add(url)

    return facts, entities, relationships, urls_visited


async def search_and_analyze_node(
    state: dict[str, Any],
    *,
    registry: LLMRegistry,
    settings: Settings,
) -> dict[str, Any]:
    """Execute pending search queries and extract structured findings in one ReAct pass."""
    writer = get_stream_writer()
    current_phase = state.get("current_phase", 1)
    writer({"node": "search_and_analyze", "status": "started", "phase": current_phase})

    pending = state.get("pending_queries", [])
    if not pending:
        writer({"node": "search_and_analyze", "status": "skipped", "reason": "no pending queries"})
        return {}

    queries_batch = pending[:MAX_QUERIES_PER_BATCH]
    remaining_queries = pending[MAX_QUERIES_PER_BATCH:]

    if remaining_queries:
        logger.info(
            "search_batch_limited",
            total_pending=len(pending),
            processing=len(queries_batch),
            remaining=len(remaining_queries),
        )

    # Build phase context for the agent's system prompt
    plan = state.get("research_plan", [])
    phase_idx = current_phase - 1
    phase_info = plan[phase_idx] if phase_idx < len(plan) else {}
    phase_context = (
        f"Phase {phase_info.get('phase_number', current_phase)} — {phase_info.get('name', f'Phase {current_phase}')}\n"
        f"Goal: {phase_info.get('description', '')}\n"
        f"Expected information types: {', '.join(phase_info.get('expected_info_types', []))}"
    )

    queries_text = "\n".join(f"- {q}" for q in queries_batch)
    user_prompt = (
        f"Target: {state['target_name']} ({state.get('target_context', '')})\n\n"
        f"Execute these queries, analyze all results, then call submit_findings:\n{queries_text}"
    )

    agent = _build_agent(
        registry,
        settings,
        phase_context=phase_context,
        supervisor_instructions=state.get("supervisor_instructions", ""),
    )

    start = time.monotonic()
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_prompt)]})
    elapsed_ms = int((time.monotonic() - start) * 1000)

    messages = result.get("messages", [])
    facts, entities, relationships, new_urls = _extract_findings(messages)

    # Merge new URLs into the existing visited set for cross-phase dedup
    urls_visited: set[str] = set(state.get("urls_visited", set())) | new_urls

    if not facts and not entities:
        logger.warning(
            "search_analyze_no_findings",
            phase=current_phase,
            queries=len(queries_batch),
            reason="submit_findings not called or returned empty",
        )

    executed = [
        {
            "query": q,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results_count": len(facts),
        }
        for q in queries_batch
    ]

    audit = AuditEntry(
        node="search_and_analyze",
        action="search_and_extract",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="google/gemini-2.5-flash",
        input_summary=(
            f"Executed {len(queries_batch)} queries"
            + (f" ({len(remaining_queries)} deferred)" if remaining_queries else "")
        ),
        output_summary=(
            f"Extracted {len(facts)} facts, {len(entities)} entities, "
            f"{len(relationships)} relationships"
        ),
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "search_and_analyze",
        "status": "complete",
        "queries_executed": len(queries_batch),
        "queries_remaining": len(remaining_queries),
        "facts": len(facts),
        "entities": len(entities),
        "relationships": len(relationships),
    })

    # current_phase_searched now signals both "searched" and "analyzed" —
    # the supervisor uses it as the gate to proceed to the verifier.
    phase_searched = len(remaining_queries) == 0

    return {
        "search_queries_executed": executed,
        "urls_visited": urls_visited,
        "pending_queries": remaining_queries,
        "extracted_facts": facts,
        "entities": entities,
        "relationships": relationships,
        "current_phase_searched": phase_searched,
        "audit_log": [audit.model_dump()],
    }
