"""Custom exception hierarchy for the research agent."""

from __future__ import annotations


class ResearchAgentError(Exception):
    """Base exception for all research agent errors."""


class LLMError(ResearchAgentError):
    """Base for model-related failures."""


class ModelTimeoutError(LLMError):
    """Model call timed out."""


class ModelRateLimitError(LLMError):
    """Model rate limit exceeded (HTTP 429)."""


class ModelResponseParsingError(LLMError):
    """Failed to parse structured output from model response."""


class SearchError(ResearchAgentError):
    """Tavily search API failure."""


class ScrapingError(ResearchAgentError):
    """Web scraping failure (HTTP errors, parsing issues)."""


class GraphDBError(ResearchAgentError):
    """Neo4j database operation failure."""


class EvaluationError(ResearchAgentError):
    """Evaluation framework error."""
