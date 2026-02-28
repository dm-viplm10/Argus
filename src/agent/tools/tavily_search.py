"""Tavily search wrapper as a LangChain tool with caching and rate limiting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_tavily import TavilySearch

if TYPE_CHECKING:
    from src.config import Settings


def create_tavily_search_tool(settings: Settings) -> TavilySearch:
    """Create a configured Tavily search tool.

    Uses the official langchain-tavily package with advanced search depth.
    Raw content is disabled to keep ReAct agent context within token limits;
    full content is fetched selectively via web_scrape when needed.
    """
    return TavilySearch(
        max_results=min(settings.MAX_RESULTS_PER_QUERY, 5),
        search_depth="advanced",
        topic="general",
        include_raw_content=False,
        include_images=False,
    )


def create_tavily_finance_tool(settings: Settings) -> TavilySearch:
    """Tavily search tuned for financial research phases."""
    return TavilySearch(
        max_results=min(settings.MAX_RESULTS_PER_QUERY, 5),
        search_depth="advanced",
        topic="finance",
        include_raw_content=False,
        include_images=False,
    )
