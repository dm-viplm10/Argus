"""Unit tests for the Verifier ReAct agent node."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_agent_result(verified_facts, unverified_claims, contradictions):
    """Build a fake ReAct agent result with a submit_verification tool call."""
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [
        {
            "name": "submit_verification",
            "args": {
                "verified_facts": verified_facts,
                "unverified_claims": unverified_claims,
                "contradictions": contradictions,
            },
        }
    ]
    # Also include a final AI message (should be ignored by extraction)
    final_msg = MagicMock()
    final_msg.tool_calls = None
    final_msg.content = "I verified everything and found issues."

    return {"messages": [tool_call_msg, final_msg]}


@pytest.fixture
def mock_verification_data():
    return {
        "verified_facts": [
            {
                "fact": "Timothy Overturf is CEO of Sisu Capital",
                "category": "professional",
                "final_confidence": 0.85,
                "verification_method": "web_verified",
                "supporting_sources": ["https://a.com", "https://b.com"],
                "contradicting_sources": [],
                "notes": "Confirmed via company website and SEC filing",
            },
        ],
        "unverified_claims": ["Claims to have MBA from Wharton"],
        "contradictions": [
            {
                "claim_a": "Founded in 2018",
                "claim_b": "Founded in 2019",
                "source_a": "https://a.com",
                "source_b": "https://b.com",
                "resolution": "2019 is from more authoritative source",
            },
        ],
    }


@pytest.mark.asyncio
async def test_verifier_active_verification(
    sample_state, mock_registry, settings, mock_verification_data
):
    """Test that the verifier extracts results from the submit_verification tool call."""
    sample_state["extracted_facts"] = [
        {"fact": "CEO of Sisu Capital", "confidence": 0.7, "source_url": "https://a.com"}
    ]

    agent_result = _make_agent_result(
        mock_verification_data["verified_facts"],
        mock_verification_data["unverified_claims"],
        mock_verification_data["contradictions"],
    )
    mock_agent = AsyncMock(return_value=agent_result)
    mock_react = MagicMock(return_value=MagicMock(ainvoke=mock_agent))

    mock_prompt = MagicMock(get_prompt=MagicMock(return_value="mock prompt"))
    with (
        patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None),
        patch("src.agent.nodes.verifier.create_react_agent", mock_react),
    ):
        from src.agent.nodes.verifier import VerifierAgent

        agent = VerifierAgent(registry=mock_registry, settings=settings, prompt_registry=mock_prompt)
        result = await agent.run(sample_state)

    assert len(result["verified_facts"]) == 1
    assert result["verified_facts"][0]["final_confidence"] == 0.85
    assert result["verified_facts"][0]["verification_method"] == "web_verified"
    assert len(result["unverified_claims"]) == 1
    assert len(result["contradictions"]) == 1
    assert result["current_phase_verified"] is True
    assert result["facts_verified_count"] == 1


@pytest.mark.asyncio
async def test_verifier_skips_when_no_facts(sample_state, mock_registry, settings):
    """Test that the verifier sets current_phase_verified=True when there are no facts (prevents infinite loop)."""
    mock_prompt = MagicMock(get_prompt=MagicMock(return_value="mock prompt"))
    with patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None):
        from src.agent.nodes.verifier import VerifierAgent

        agent = VerifierAgent(registry=mock_registry, settings=settings, prompt_registry=mock_prompt)
        result = await agent.run(sample_state)

    assert result == {"current_phase_verified": True}


@pytest.mark.asyncio
async def test_verifier_delta_cursor(sample_state, mock_registry, settings, mock_verification_data):
    """Test that the verifier only processes facts after the cursor position."""
    sample_state["extracted_facts"] = [
        {"fact": "Old fact 1", "confidence": 0.8},
        {"fact": "Old fact 2", "confidence": 0.9},
        {"fact": "New fact 3", "confidence": 0.5},
    ]
    sample_state["facts_verified_count"] = 2  # skip first 2

    agent_result = _make_agent_result(
        mock_verification_data["verified_facts"],
        [],
        [],
    )
    mock_agent = AsyncMock(return_value=agent_result)
    mock_react = MagicMock(return_value=MagicMock(ainvoke=mock_agent))

    mock_prompt = MagicMock(get_prompt=MagicMock(return_value="mock prompt"))
    with (
        patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None),
        patch("src.agent.nodes.verifier.create_react_agent", mock_react),
    ):
        from src.agent.nodes.verifier import VerifierAgent

        agent = VerifierAgent(registry=mock_registry, settings=settings, prompt_registry=mock_prompt)
        result = await agent.run(sample_state)

    # The user prompt should only contain the 1 new fact (index 2)
    call_args = mock_agent.call_args
    messages = call_args[0][0]["messages"]
    user_msg_content = messages[0].content
    assert "New fact 3" in user_msg_content
    assert "Old fact 1" not in user_msg_content

    # Cursor should advance to 3
    assert result["facts_verified_count"] == 3


@pytest.mark.asyncio
async def test_verifier_extracts_from_tool_call_not_free_text(
    sample_state, mock_registry, settings
):
    """Verify extraction comes from tool_call args, not the agent's final text message."""
    sample_state["extracted_facts"] = [
        {"fact": "Has a patent", "confidence": 0.5, "source_url": "https://x.com"}
    ]

    # Tool call has "web_verified", but the free-text message says something different
    tool_call_msg = MagicMock()
    tool_call_msg.tool_calls = [
        {
            "name": "submit_verification",
            "args": {
                "verified_facts": [
                    {"fact": "Has a patent", "final_confidence": 0.92,
                     "verification_method": "web_verified",
                     "category": "professional",
                     "supporting_sources": ["https://patents.google.com/test"],
                     "contradicting_sources": [],
                     "notes": "Verified via Google Patents"},
                ],
                "unverified_claims": [],
                "contradictions": [],
            },
        }
    ]
    final_msg = MagicMock()
    final_msg.tool_calls = None
    final_msg.content = "I could not verify the patent."  # contradicts the tool call

    mock_agent = AsyncMock(return_value={"messages": [tool_call_msg, final_msg]})
    mock_react = MagicMock(return_value=MagicMock(ainvoke=mock_agent))

    mock_prompt = MagicMock(get_prompt=MagicMock(return_value="mock prompt"))
    with (
        patch("src.agent.nodes.verifier.get_stream_writer", return_value=lambda x: None),
        patch("src.agent.nodes.verifier.create_react_agent", mock_react),
    ):
        from src.agent.nodes.verifier import VerifierAgent

        agent = VerifierAgent(registry=mock_registry, settings=settings, prompt_registry=mock_prompt)
        result = await agent.run(sample_state)

    # Should use tool call data (0.92), not the misleading text
    assert result["verified_facts"][0]["final_confidence"] == 0.92
    assert result["verified_facts"][0]["verification_method"] == "web_verified"
