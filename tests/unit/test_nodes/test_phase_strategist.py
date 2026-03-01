"""Unit tests for the phase_strategist node."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import PhaseStrategyDecision, ResearchPhase


@pytest.fixture
def phase_1_complete_state(sample_state) -> dict:
    """State after Phase 1 (surface) has completed."""
    return {
        **sample_state,
        "current_phase": 1,
        "max_phases": 1,
        "dynamic_phases": True,
        "phase_complete": True,
        "research_plan": [
            {
                "phase_number": 1,
                "name": "Surface Layer",
                "description": "Basic bio and professional profiles",
                "queries": ["query1", "query2"],
                "expected_info_types": ["biographical"],
                "priority": 1,
            }
        ],
        "extracted_facts": [
            {"fact": "CEO of Acme Corp", "category": "professional", "confidence": 0.9, "source_url": "https://acme.com"},
        ],
        "entities": [{"name": "Acme Corp", "type": "organization", "attributes": {}, "sources": []}],
        "verified_facts": [],
        "risk_flags": [{"flag": "Unverified education claim", "severity": "medium", "category": "reputational"}],
    }


@pytest.fixture
def mock_phase_strategist_response_add_phases():
    """Mock LLM response: add corporate and legal phases."""
    return PhaseStrategyDecision(
        action="add_phases",
        phases_to_add=[
            ResearchPhase(
                phase_number=2,
                name="Corporate Structure",
                description="Verify Acme Corp filings",
                queries=["Acme Corp SEC filings", "Acme Corp state registration"],
                expected_info_types=["corporate"],
                priority=2,
            ),
            ResearchPhase(
                phase_number=3,
                name="Legal & Regulatory",
                description="Check for legal issues",
                queries=["target name court records", "target name regulatory"],
                expected_info_types=["legal"],
                priority=3,
            ),
        ],
        reasoning="Corporate entities and risk flags suggest deeper corporate and legal investigation.",
    )


@pytest.fixture
def mock_phase_strategist_response_synthesize():
    """Mock LLM response: proceed to synthesis."""
    return PhaseStrategyDecision(
        action="synthesize",
        phases_to_add=[],
        reasoning="Phase 1 provides sufficient coverage; no deeper phases warranted.",
    )


@pytest.mark.asyncio
async def test_phase_strategist_adds_phases(
    phase_1_complete_state,
    mock_router,
    mock_phase_strategist_response_add_phases,
):
    """When strategist returns add_phases, state is updated with new phases."""
    mock_router.invoke = AsyncMock(return_value=mock_phase_strategist_response_add_phases)

    with patch("src.agent.nodes.phase_strategist.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.phase_strategist import phase_strategist_node

        result = await phase_strategist_node(phase_1_complete_state, router=mock_router)

    assert "research_plan" in result
    plan = result["research_plan"]
    assert len(plan) == 3  # original Phase 1 + 2 new phases
    assert plan[1]["name"] == "Corporate Structure"
    assert plan[2]["name"] == "Legal & Regulatory"
    assert result["max_phases"] == 3
    assert result["current_phase"] == 2
    assert result["dynamic_phases"] is False
    assert result["pending_queries"]
    assert result["phase_complete"] is False


@pytest.mark.asyncio
async def test_phase_strategist_synthesizes(
    phase_1_complete_state,
    mock_router,
    mock_phase_strategist_response_synthesize,
):
    """When strategist returns synthesize, no phases are added; state ready for synthesizer."""
    mock_router.invoke = AsyncMock(return_value=mock_phase_strategist_response_synthesize)

    with patch("src.agent.nodes.phase_strategist.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.phase_strategist import phase_strategist_node

        result = await phase_strategist_node(phase_1_complete_state, router=mock_router)

    assert "research_plan" not in result
    assert "max_phases" not in result
    assert result["audit_log"][0]["output_summary"].startswith("synthesize")
