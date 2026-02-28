"""Unit tests for the model router with fallback logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.model_router import ModelRouter


@pytest.mark.asyncio
async def test_router_invokes_primary_model(mock_registry):
    from langchain_core.messages import HumanMessage

    mock_result = MagicMock()
    mock_result.content = "test"
    mock_result.usage_metadata = None
    mock_registry.get_model("supervisor").ainvoke = AsyncMock(return_value=mock_result)

    router = ModelRouter(mock_registry)

    with patch("src.models.model_router.traceable", lambda **kw: lambda f: f):
        result = await router.invoke("supervisor", [HumanMessage(content="test")])

    assert result is mock_result


@pytest.mark.asyncio
async def test_router_falls_back_on_failure(mock_registry):
    from langchain_core.messages import HumanMessage

    mock_registry.get_model("supervisor").ainvoke = AsyncMock(side_effect=RuntimeError("timeout"))

    fallback_result = MagicMock()
    fallback_result.content = "fallback response"
    fallback_result.usage_metadata = None

    fallback_model = MagicMock()
    fallback_model.ainvoke = AsyncMock(return_value=fallback_result)
    fallback_model.model_name = "fallback"
    mock_registry.get_fallback_chain = MagicMock(return_value=[fallback_model])

    router = ModelRouter(mock_registry)

    with patch("src.models.model_router.traceable", lambda **kw: lambda f: f):
        result = await router.invoke("supervisor", [HumanMessage(content="test")])

    assert result is fallback_result
