"""Search & Scrape ReAct agent — executes Tavily searches and scrapes high-value URLs."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent

from src.agent.tools.tavily_search import create_tavily_search_tool
from src.agent.tools.web_scrape import WebScrapeTool
from src.config import Settings
from src.models.llm_registry import LLMRegistry
from src.models.schemas import AuditEntry
from src.utils.logging import get_logger

logger = get_logger(__name__)

SEARCH_AGENT_PROMPT = """\
You are a web research specialist. Execute search queries and scrape high-value
URLs to gather comprehensive information about the target.

Target: {target_name} ({target_context})

Queries to execute:
{queries}

Instructions:
1. Execute each query using the tavily search tool.
2. Review the results — identify which URLs contain the most relevant information.
3. For important URLs where raw content is missing or insufficient, use the
   web_scrape tool to get full content.
4. Focus on official sources, news articles, regulatory filings, and professional profiles.
5. Avoid scraping the same URL twice.

Do your best to execute ALL queries and gather thorough results.
"""


def build_search_agent(registry: LLMRegistry, settings: Settings):
    """Create the ReAct search agent with Tavily and scrape tools."""
    model = registry.get_model("query_refiner")
    tavily_tool = create_tavily_search_tool(settings)
    scrape_tool = WebScrapeTool()

    return create_react_agent(
        model=model,
        tools=[tavily_tool, scrape_tool],
    )


async def search_and_scrape_node(
    state: dict[str, Any],
    *,
    registry: LLMRegistry,
    settings: Settings,
) -> dict[str, Any]:
    """Execute pending search queries via ReAct agent."""
    writer = get_stream_writer()
    writer({"node": "search_and_scrape", "status": "started"})

    pending = state.get("pending_queries", [])
    if not pending:
        writer({"node": "search_and_scrape", "status": "skipped", "reason": "no pending queries"})
        return {}

    queries_text = "\n".join(f"- {q}" for q in pending)
    prompt = SEARCH_AGENT_PROMPT.format(
        target_name=state["target_name"],
        target_context=state.get("target_context", ""),
        queries=queries_text,
    )

    agent = build_search_agent(registry, settings)

    start = time.monotonic()
    result = await agent.ainvoke({
        "messages": [
            SystemMessage(content="You are a thorough web researcher."),
            HumanMessage(content=prompt),
        ],
    })
    elapsed_ms = int((time.monotonic() - start) * 1000)

    messages = result.get("messages", [])
    search_results: list[dict] = []
    scraped_content: list[dict] = []
    urls_visited: set[str] = set(state.get("urls_visited", set()))

    for msg in messages:
        if hasattr(msg, "tool_calls"):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content:
            search_results.append({
                "content": content[:10_000],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    executed = [
        {
            "query": q,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results_count": len(search_results),
        }
        for q in pending
    ]

    audit = AuditEntry(
        node="search_and_scrape",
        action="execute_searches",
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_used="openai/gpt-4.1-mini",
        input_summary=f"Executed {len(pending)} queries",
        output_summary=f"Collected {len(search_results)} result blocks",
        duration_ms=elapsed_ms,
    )

    writer({
        "node": "search_and_scrape",
        "status": "complete",
        "queries_executed": len(pending),
        "results_collected": len(search_results),
    })

    return {
        "search_queries_executed": executed,
        "search_results": search_results,
        "scraped_content": scraped_content,
        "urls_visited": urls_visited,
        "pending_queries": [],
        "audit_log": [audit.model_dump()],
    }
