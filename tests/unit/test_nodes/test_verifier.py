"""Unit tests for the Verifier node."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import Contradiction, VerifiedFact, VerifierOutput


@pytest.fixture
def mock_verifier_output():
    return VerifierOutput(
        verified_facts=[
            VerifiedFact(
                fact="Timothy Overturf is CEO of Sisu Capital",
                category="professional",
                final_confidence=0.85,
                supporting_sources=["https://a.com", "https://b.com"],
                notes="Confirmed by two independent sources",
            ),
        ],
        unverified_claims=["Claims to have MBA from Wharton"],
        contradictions=[
            Contradiction(
                claim_a="Founded in 2018",
                claim_b="Founded in 2019",
                source_a="https://a.com",
                source_b="https://b.com",
                resolution="2019 is from more authoritative source",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_verifier_scores_facts(sample_state, mock_router, mock_verifier_output):
    sample_state["extracted_facts"] = [
        {"fact": "CEO of Sisu Capital", "confidence": 0.7, "source_url": "https://a.com"}
    ]
    mock_router.invoke = AsyncMock(return_value=mock_verifier_output)

    with patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.verifier import verifier_node

        result = await verifier_node(sample_state, router=mock_router)

    assert len(result["verified_facts"]) == 1
    assert result["verified_facts"][0]["final_confidence"] == 0.85
    assert len(result["unverified_claims"]) == 1
    assert len(result["contradictions"]) == 1


@pytest.mark.asyncio
async def test_verifier_skips_when_no_facts(sample_state, mock_router):
    with patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.verifier import verifier_node

        result = await verifier_node(sample_state, router=mock_router)

    assert result == {}
