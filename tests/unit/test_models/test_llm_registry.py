"""Unit tests for the LLM registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_registry_creates_all_models(settings):
    with patch("src.models.llm_registry.ChatOpenAI") as MockChat:
        MockChat.return_value = MockChat
        from src.models.llm_registry import LLMRegistry, MODEL_CONFIG

        registry = LLMRegistry(settings)

        for task in MODEL_CONFIG:
            model = registry.get_model(task)
            assert model is not None


def test_registry_raises_for_unknown_task(settings):
    with patch("src.models.llm_registry.ChatOpenAI"):
        from src.models.llm_registry import LLMRegistry

        registry = LLMRegistry(settings)

        with pytest.raises(KeyError, match="nonexistent"):
            registry.get_model("nonexistent")


def test_registry_returns_fallback(settings):
    with patch("src.models.llm_registry.ChatOpenAI") as MockChat:
        MockChat.return_value = MockChat
        from src.models.llm_registry import LLMRegistry

        registry = LLMRegistry(settings)
        fallback = registry.get_fallback("supervisor")
        assert fallback is not None


def test_registry_tracks_usage(settings):
    with patch("src.models.llm_registry.ChatOpenAI"):
        from src.models.llm_registry import LLMRegistry

        registry = LLMRegistry(settings)
        registry.record_usage("supervisor", 100, 0.01)

        stats = registry.stats
        assert stats["supervisor"]["calls"] == 1
        assert stats["supervisor"]["tokens"] == 100
