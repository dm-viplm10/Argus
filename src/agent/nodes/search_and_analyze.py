"""Search & Analyze ReAct agent — executes searches, scrapes, and extracts structured findings."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from src.agent.base import ReActAgent
from src.agent.tools.tavily_search import create_tavily_search_tool
from src.agent.tools.web_scrape import WebScrapeTool
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

MAX_QUERIES_PER_BATCH = 6


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


def _extract_findings(messages: list) -> tuple[list[dict], list[dict], list[dict], set[str]]:
    """Pull structured findings and visited URLs from tool_call args."""
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


class SearchAndAnalyzeAgent(ReActAgent):
    """Execute pending search queries and extract structured findings in one ReAct pass."""

    name = "search_and_analyze"
    task = "search_and_analyze"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute pending search queries and extract structured findings."""
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

        plan = state.get("research_plan", [])
        phase_idx = current_phase - 1
        phase_info = plan[phase_idx] if phase_idx < len(plan) else {}
        phase_context = (
            f"Phase {phase_info.get('phase_number', current_phase)} — {phase_info.get('name', f'Phase {current_phase}')}\n"
            f"Goal: {phase_info.get('description', '')}\n"
            f"Expected information types: {', '.join(phase_info.get('expected_info_types', []))}"
        )

        system_prompt = self._prompt_registry.get_prompt(
            "search_and_analyze",
            phase_context=phase_context,
            supervisor_instructions=state.get("supervisor_instructions", "") or "No specific instructions.",
        )

        model = self._registry.get_model("search_and_analyze")
        tavily_tool = create_tavily_search_tool(self._settings)
        scrape_tool = WebScrapeTool()

        agent = create_react_agent(
            model=model,
            tools=[tavily_tool, scrape_tool, submit_findings],
            prompt=SystemMessage(content=system_prompt),
        )

        queries_text = "\n".join(f"- {q}" for q in queries_batch)
        user_prompt = (
            f"Target: {state['target_name']} ({state.get('target_context', '')})\n\n"
            f"1) Execute these queries with tavily_search. 2) For promising URLs in the results, call web_scrape to fetch full content — do not just list URLs and stop. "
            f"3) After gathering content, call submit_findings with your findings. Your final tool call must be submit_findings — no text-only conclusion.\n\n"
            f"Queries to execute:\n{queries_text}"
        )

        start = time.monotonic()
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_prompt)]})
        elapsed_ms = int((time.monotonic() - start) * 1000)

        messages = result.get("messages", [])
        facts, entities, relationships, new_urls = _extract_findings(messages)

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
