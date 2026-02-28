"""Unit tests for the Risk Assessor node."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import RiskAssessment, RiskFlag


@pytest.fixture
def mock_risk_output():
    return RiskAssessment(
        risk_flags=[
            RiskFlag(
                flag="Potential credential misrepresentation",
                category="behavioral",
                severity="medium",
                confidence=0.6,
                evidence=["LinkedIn claims not verified by university"],
                source_urls=["https://linkedin.com/test"],
                recommended_followup="Verify educational credentials directly",
            ),
        ],
        overall_risk_score=0.4,
        summary="Moderate risk profile with one behavioral flag.",
    )


@pytest.mark.asyncio
async def test_risk_assessor_flags_risks(sample_state, mock_router, mock_risk_output):
    sample_state["verified_facts"] = [
        {"fact": "CEO of Sisu Capital", "final_confidence": 0.85}
    ]
    mock_router.invoke = AsyncMock(return_value=mock_risk_output)

    with patch("src.agent.nodes.risk_assessor.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.risk_assessor import risk_assessor_node

        result = await risk_assessor_node(sample_state, router=mock_router)

    assert len(result["risk_flags"]) == 1
    assert result["risk_flags"][0]["severity"] == "medium"
    assert result["overall_risk_score"] == 0.4


@pytest.mark.asyncio
async def test_risk_assessor_skips_when_no_facts(sample_state, mock_router):
    with patch("src.agent.nodes.risk_assessor.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.risk_assessor import risk_assessor_node

        result = await risk_assessor_node(sample_state, router=mock_router)

    assert result == {}
