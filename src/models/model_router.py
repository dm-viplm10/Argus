"""Model router with fallback chains and LangSmith tracing on fallback events."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from langsmith import traceable

from src.models.llm_registry import LLMRegistry
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = get_logger(__name__)


class ModelRouter:
    """Wraps model calls with automatic fallback and usage tracking."""

    def __init__(self, registry: LLMRegistry) -> None:
        self._registry = registry
        # Populated after every invoke() call so nodes can read token usage.
        self._last_usage: dict[str, int | float] = {"tokens": 0, "cost": 0.0}

    @property
    def last_usage(self) -> dict[str, int | float]:
        """Token and cost data from the most recent invoke() call."""
        return dict(self._last_usage)

    @traceable(run_type="chain", name="model_router_invoke")
    async def invoke(
        self,
        task: str,
        messages: list[BaseMessage],
        *,
        structured_output: type | None = None,
    ) -> object:
        """Invoke the model for a task, falling back on failure.

        Args:
            task: The task name (e.g. "analyzer", "verifier").
            messages: Chat messages to send.
            structured_output: Optional Pydantic model class for structured output.

        Returns:
            The model response (AIMessage or structured Pydantic object).
        """
        primary = self._registry.get_model(task)
        fallbacks = self._registry.get_fallback_chain(task)
        all_models = [("primary", primary), *((f"fallback-{i}", fb) for i, fb in enumerate(fallbacks))]

        last_error: Exception | None = None
        for label, model in all_models:
            try:
                target = model
                if structured_output is not None:
                    target = model.with_structured_output(structured_output)

                start = time.monotonic()
                result = await target.ainvoke(messages)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                # Token counts come from LangChain's usage_metadata, which is populated
                # automatically by the SDK for every model call. LangSmith (via
                # LANGCHAIN_TRACING_V2=True) is the source of truth for cost analytics;
                # we only extract token counts here for lightweight in-app audit entries.
                tokens = 0
                usage_meta = getattr(result, "usage_metadata", None)
                if usage_meta:
                    tokens = getattr(usage_meta, "total_tokens", 0) or 0

                self._last_usage = {"tokens": tokens, "cost": 0.0}
                self._registry.record_usage(task, tokens, 0.0)

                if label != "primary":
                    logger.warning(
                        "model_fallback_used",
                        task=task,
                        label=label,
                        model=model.model_name,
                        elapsed_ms=elapsed_ms,
                    )
                else:
                    logger.debug(
                        "model_invoked",
                        task=task,
                        model=model.model_name,
                        tokens=tokens,
                        elapsed_ms=elapsed_ms,
                    )

                return result

            except Exception as exc:
                last_error = exc
                self._last_usage = {"tokens": 0, "cost": 0.0}
                logger.error(
                    "model_invoke_failed",
                    task=task,
                    label=label,
                    model=model.model_name,
                    error=str(exc),
                )
                continue

        raise RuntimeError(
            f"All models failed for task '{task}': {last_error}"
        ) from last_error
