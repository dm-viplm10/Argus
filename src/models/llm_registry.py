"""Multi-model LLM registry via OpenRouter.

All models are accessed through OpenRouter's OpenAI-compatible API.
Each task maps to a specific model chosen for that task's characteristics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI

from src.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    slug: str
    temperature: float
    purpose: str
    max_tokens: int | None = None


MODEL_CONFIG: dict[str, ModelSpec] = {
    "supervisor": ModelSpec(
        slug="openai/gpt-4.1",
        temperature=0.1,
        purpose="Agent orchestration and routing decisions",
    ),
    "planner": ModelSpec(
        slug="anthropic/claude-sonnet-4.6",
        temperature=0.3,
        purpose="Research plan generation",
    ),
    "phase_strategist": ModelSpec(
        slug="openai/gpt-4.1",
        temperature=0.3,
        purpose="Dynamic phase strategy based on Phase 1 findings",
    ),
    "query_refiner": ModelSpec(
        slug="openai/gpt-4.1-mini",
        temperature=0.4,
        purpose="Search query generation",
    ),
    "search_and_analyze": ModelSpec(
        slug="google/gemini-2.5-flash",
        temperature=0.1,
        purpose="Web research and structured fact/entity extraction in one ReAct pass",
    ),
    "verifier": ModelSpec(
        slug="google/gemini-2.5-flash",
        temperature=0.5,
        purpose="Active fact verification via web search and reasoning",
    ),
    "risk_assessor": ModelSpec(
        slug="google/gemini-2.5-pro",
        temperature=0.5,
        purpose="Unfiltered risk and red flag identification",
    ),
    "synthesizer": ModelSpec(
        slug="anthropic/claude-sonnet-4.6",
        temperature=0.2,
        purpose="Final report generation",
    ),
    "evaluator": ModelSpec(
        slug="openai/gpt-4.1",
        temperature=0.6,
        purpose="LLM-as-judge evaluation scoring against ground truth",
    ),
}

FALLBACK_CHAINS: dict[str, list[str]] = {
    "anthropic/claude-sonnet-4.6": ["openai/gpt-4.1", "google/gemini-2.5-pro"],
    "openai/gpt-4.1": ["anthropic/claude-sonnet-4.6", "google/gemini-2.5-pro"],
    "openai/gpt-4.1-mini": ["google/gemini-2.5-flash", "anthropic/claude-sonnet-4.6"],
    "google/gemini-2.5-pro": ["anthropic/claude-sonnet-4.6", "openai/gpt-4.1"],
    "google/gemini-2.5-flash": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4.6"],
}


class LLMRegistry:
    """Manages multiple LLM instances via OpenRouter with fallback support."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models: dict[str, ChatOpenAI] = {}
        self._slug_cache: dict[str, ChatOpenAI] = {}
        self._call_stats: dict[str, dict] = {}

        for task_name, spec in MODEL_CONFIG.items():
            self._models[task_name] = self._build_model(spec)
            self._call_stats[task_name] = {"calls": 0, "tokens": 0, "cost": 0.0}

    def _build_model(self, spec: ModelSpec) -> ChatOpenAI:
        cache_key = f"{spec.slug}:{spec.temperature}:{spec.max_tokens}"
        if cache_key in self._slug_cache:
            return self._slug_cache[cache_key]

        kwargs: dict = {
            "model": spec.slug,
            "openai_api_key": self._settings.OPENROUTER_API_KEY,
            "openai_api_base": self._settings.OPENROUTER_BASE_URL,
            "temperature": spec.temperature,
            "model_kwargs": {
                "extra_headers": {
                    "HTTP-Referer": "https://argus-agent.local",
                    "X-Title": "Argus",
                }
            },
        }
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens

        model = ChatOpenAI(**kwargs)
        self._slug_cache[cache_key] = model
        return model

    def get_model(self, task: str) -> ChatOpenAI:
        """Get the primary model assigned to a task."""
        if task not in self._models:
            raise KeyError(f"No model registered for task '{task}'")
        return self._models[task]

    def get_fallback(self, task: str) -> ChatOpenAI | None:
        """Get the first fallback model for a task."""
        spec = MODEL_CONFIG.get(task)
        if spec is None:
            return None
        chain = FALLBACK_CHAINS.get(spec.slug, [])
        if not chain:
            return None
        fallback_spec = ModelSpec(
            slug=chain[0],
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            purpose=f"Fallback for {task}",
        )
        return self._build_model(fallback_spec)

    def get_fallback_chain(self, task: str) -> list[ChatOpenAI]:
        """Return all fallback models for a task, in order."""
        spec = MODEL_CONFIG.get(task)
        if spec is None:
            return []
        chain = FALLBACK_CHAINS.get(spec.slug, [])
        result: list[ChatOpenAI] = []
        for slug in chain:
            fb_spec = ModelSpec(
                slug=slug,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                purpose=f"Fallback for {task}",
            )
            result.append(self._build_model(fb_spec))
        return result

    def record_usage(self, task: str, tokens: int, cost: float) -> None:
        if task in self._call_stats:
            self._call_stats[task]["calls"] += 1
            self._call_stats[task]["tokens"] += tokens
            self._call_stats[task]["cost"] += cost

    @property
    def stats(self) -> dict[str, dict]:
        return dict(self._call_stats)
