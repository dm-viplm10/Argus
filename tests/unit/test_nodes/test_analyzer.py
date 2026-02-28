"""Unit tests for the Analyzer node."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import (
    AnalyzerOutput,
    ExtractedEntity,
    ExtractedFact,
    ExtractedRelationship,
)


@pytest.fixture
def mock_analyzer_output():
    return AnalyzerOutput(
        facts=[
            ExtractedFact(
                fact="Timothy Overturf is CEO of Sisu Capital",
                category="professional",
                confidence=0.8,
                source_url="https://example.com/article",
                source_type="news",
            ),
        ],
        entities=[
            ExtractedEntity(
                name="Timothy Overturf",
                type="person",
                attributes={"role": "CEO"},
                sources=["https://example.com/article"],
            ),
            ExtractedEntity(
                name="Sisu Capital",
                type="organization",
                attributes={"type": "investment firm"},
                sources=["https://example.com/article"],
            ),
        ],
        relationships=[
            ExtractedRelationship(
                source_entity="Timothy Overturf",
                target_entity="Sisu Capital",
                relationship_type="WORKS_AT",
                evidence="Article states he is CEO",
                confidence=0.8,
                source_url="https://example.com/article",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_analyzer_extracts_structured_data(sample_state, mock_router, mock_analyzer_output):
    sample_state["search_results"] = [{"content": "Timothy Overturf, CEO of Sisu Capital..."}]
    mock_router.invoke = AsyncMock(return_value=mock_analyzer_output)

    with patch("src.agent.nodes.analyzer.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.analyzer import analyzer_node

        result = await analyzer_node(sample_state, router=mock_router)

    assert len(result["extracted_facts"]) == 1
    assert len(result["entities"]) == 2
    assert len(result["relationships"]) == 1
    assert result["extracted_facts"][0]["category"] == "professional"


@pytest.mark.asyncio
async def test_analyzer_skips_when_no_content(sample_state, mock_router):
    with patch("src.agent.nodes.analyzer.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.analyzer import analyzer_node

        result = await analyzer_node(sample_state, router=mock_router)

    assert result == {}
