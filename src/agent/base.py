"""Agent base abstractions for research graph nodes.

BaseAgent is the protocol all nodes implement. StructuredOutputAgent, ReActAgent,
and ToolNode are concrete base classes for different agent patterns.

Shared helpers on the base classes eliminate the boilerplate that would otherwise
be repeated in every node:

- ``BaseAgent._get_model_slug()`` — looks up the configured model slug from
  MODEL_CONFIG so nodes never hardcode strings.
- ``BaseAgent._build_audit()`` — builds the ``{"audit_log": [...]}`` dict ready
  for state merge, centralising AuditEntry construction and UTC timestamp generation.
- ``StructuredOutputAgent._invoke_structured()`` — wraps ``router.invoke`` with
  wall-time measurement and usage extraction.
- ``ReActAgent._run_react_agent()`` — wraps a compiled ReAct agent's ``ainvoke``
  with wall-time measurement, returning ``(messages, elapsed_ms)``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage

from src.models.llm_registry import MODEL_CONFIG
from src.models.schemas import AuditEntry


class BaseAgent(ABC):
    """Abstract base for research graph nodes. Single Responsibility: execute one step."""

    name: str = ""

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Process state, return updates. Must include audit_log when applicable."""
        ...

    def _get_model_slug(self) -> str:
        """Return the configured model slug for this node from MODEL_CONFIG.

        Falls back to ``"unknown"`` if the node is not registered, preventing
        stale hardcoded strings in audit entries.
        """
        spec = MODEL_CONFIG.get(self.name)
        return spec.slug if spec else "unknown"

    def _build_audit(
        self,
        *,
        action: str,
        output_summary: str = "",
        duration_ms: int = 0,
        model_used: str = "",
        tokens_consumed: int = 0,
        cost_usd: float = 0.0,
        input_summary: str = "",
    ) -> dict:
        """Build and return ``{"audit_log": [...]}`` ready for a state merge.

        Centralises AuditEntry construction so nodes only provide the values
        that vary per invocation. ``node`` is always ``self.name`` and
        ``timestamp`` is always the current UTC time.

        Use ``**self._build_audit(...)`` to spread the result directly into
        a return dict::

            return {
                "some_field": value,
                **self._build_audit(action="do_thing", output_summary="done"),
            }
        """
        return {
            "audit_log": [
                AuditEntry(
                    node=self.name,
                    action=action,
                    timestamp=datetime.now(UTC).isoformat(),
                    model_used=model_used,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    duration_ms=duration_ms,
                    tokens_consumed=tokens_consumed,
                    cost_usd=cost_usd,
                ).model_dump()
            ]
        }


class StructuredOutputAgent(BaseAgent):
    """Base for agents that use router.invoke with structured_output schema."""

    task: str = ""

    def __init__(self, *, router: Any, prompt_registry: Any) -> None:
        self._router = router
        self._prompt_registry = prompt_registry

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]: ...

    async def _invoke_structured(
        self,
        messages: list,
        schema: type | None = None,
    ) -> tuple[Any, int, dict]:
        """Invoke the router, measure wall time, return ``(result, elapsed_ms, usage)``.

        Args:
            messages: The message list to pass to the router (typically
                ``[SystemMessage(...), HumanMessage(...)]``).
            schema: Optional Pydantic model for structured output parsing.
                When ``None`` the router returns raw content (e.g. synthesizer).

        Returns:
            A 3-tuple of the router result, elapsed milliseconds, and the
            ``last_usage`` dict (``{"tokens": int, "cost": float}``).
        """
        start = time.monotonic()
        kwargs = {"structured_output": schema} if schema is not None else {}
        result = await self._router.invoke(self.name, messages, **kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return result, elapsed_ms, self._router.last_usage


class ReActAgent(BaseAgent):
    """Base for ReAct agents (search_and_analyze, verifier) using create_react_agent."""

    task: str = ""

    def __init__(self, *, registry: Any, settings: Any, prompt_registry: Any) -> None:
        self._registry = registry
        self._settings = settings
        self._prompt_registry = prompt_registry

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]: ...

    async def _run_react_agent(
        self,
        agent: Any,
        user_prompt: str,
        config: dict | None = None,
    ) -> tuple[list, int]:
        """Invoke a compiled ReAct agent, return ``(messages, elapsed_ms)``.

        Args:
            agent: A compiled LangGraph ReAct agent (from ``create_react_agent``).
            user_prompt: The user-turn text; wrapped in a ``HumanMessage`` internally.
            config: Optional LangGraph run config (e.g. ``{"recursion_limit": N}``).

        Returns:
            A 2-tuple of the output message list and elapsed milliseconds.
        """
        start = time.monotonic()
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_prompt)]},
            config=config,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return result.get("messages", []), elapsed_ms


class ToolNode(BaseAgent):
    """Base for pure code nodes (no LLM) like graph_builder."""

    def __init__(self, *, neo4j_conn: Any) -> None:
        self._neo4j_conn = neo4j_conn

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]: ...
