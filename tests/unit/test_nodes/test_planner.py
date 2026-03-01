"""Unit tests for the Planner node."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schemas import ResearchPlan, ResearchPhase


@pytest.fixture
def mock_plan():
    return ResearchPlan(
        phases=[
            ResearchPhase(
                phase_number=1,
                name="Surface Layer",
                description="Basic bio and company info",
                queries=["Timothy Overturf CEO", "Sisu Capital"],
                expected_info_types=["biographical", "professional"],
                priority=1,
            ),
            ResearchPhase(
                phase_number=2,
                name="Corporate Structure",
                description="SEC filings and registrations",
                queries=["Sisu Capital SEC filing", "Sisu Capital registration"],
                expected_info_types=["financial"],
                priority=2,
            ),
        ],
        total_estimated_queries=4,
        rationale="Standard due diligence progression",
    )


@pytest.mark.asyncio
async def test_planner_returns_structured_plan(sample_state, mock_router, mock_plan):
    mock_router.invoke = AsyncMock(return_value=mock_plan)

    with patch("src.agent.nodes.planner.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.planner import planner_node

        result = await planner_node(sample_state, router=mock_router)

    assert "research_plan" in result
    assert len(result["research_plan"]) == 2
    assert result["current_phase"] == 1
    assert len(result["audit_log"]) == 1
    assert result["audit_log"][0]["node"] == "planner"


@pytest.mark.asyncio
async def test_planner_handles_empty_response(sample_state, mock_router):
    mock_router.invoke = AsyncMock(return_value="invalid")

    with patch("src.agent.nodes.planner.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.planner import planner_node

        result = await planner_node(sample_state, router=mock_router)

    assert result["research_plan"] == []
