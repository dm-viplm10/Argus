"""Agent base abstractions for research graph nodes.

BaseAgent is the protocol all nodes implement. StructuredOutputAgent, ReActAgent,
and ToolNode are concrete base classes for different agent patterns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """Abstract base for research graph nodes. Single Responsibility: execute one step."""

    name: str = ""

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Process state, return updates. Must include audit_log when applicable."""
        ...


class StructuredOutputAgent(BaseAgent):
    """Base for agents that use router.invoke with structured_output schema."""

    task: str = ""

    def __init__(self, *, router: Any, prompt_registry: Any) -> None:
        self._router = router
        self._prompt_registry = prompt_registry

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the structured output agent. Subclasses override _build_prompt_kwargs and _build_updates."""
        raise NotImplementedError


class ReActAgent(BaseAgent):
    """Base for ReAct agents (search_and_analyze, verifier) using create_react_agent."""

    task: str = ""

    def __init__(self, *, registry: Any, settings: Any, prompt_registry: Any) -> None:
        self._registry = registry
        self._settings = settings
        self._prompt_registry = prompt_registry

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the ReAct agent. Subclasses override _build_system_prompt, _build_user_message, _get_tools, _extract_output."""
        raise NotImplementedError


class ToolNode(BaseAgent):
    """Base for pure code nodes (no LLM) like graph_builder."""

    def __init__(self, *, neo4j_conn: Any) -> None:
        self._neo4j_conn = neo4j_conn

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the tool node. Subclasses override with their logic."""
        raise NotImplementedError
