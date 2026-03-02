"""Prompt registry — central access to all agent prompt templates.

Loads templates from .md files in the templates/ subdirectory.
Uses string.Template ($variable) for substitution so markdown files
can contain literal JSON braces without escaping.
"""

from __future__ import annotations

import string
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# All task names that must have a corresponding .md template file.
# Used by validate_all() to detect missing templates at startup rather than
# mid-pipeline (after LLM budget has already been spent).
_REQUIRED_TASKS: frozenset[str] = frozenset({
    "supervisor",
    "planner",
    "phase_strategist",
    "query_refiner",
    "search_and_analyze",
    "verifier",
    "risk_assessor",
    "synthesizer",
})


class PromptRegistry:
    """Registry for agent prompt templates. Loads from .md files on first use."""

    def __init__(self) -> None:
        self._cache: dict[str, string.Template] = {}

    def _load(self, task: str) -> string.Template:
        if task not in self._cache:
            path = _TEMPLATES_DIR / f"{task}.md"
            if not path.exists():
                raise KeyError(f"No prompt template found for task '{task}' at {path}")
            self._cache[task] = string.Template(path.read_text(encoding="utf-8"))
        return self._cache[task]

    def get_prompt(self, task: str, **kwargs: object) -> str:
        """Return the formatted prompt for the given task."""
        return self._load(task).substitute(kwargs)

    def validate_all(self) -> None:
        """Eagerly load and validate every required template.

        Call this once at application startup so missing or malformed template
        files raise immediately, before any research run is attempted.

        Raises:
            KeyError: If a required template file is absent.
            ValueError: If ``string.Template`` cannot parse the file contents.
        """
        for task in sorted(_REQUIRED_TASKS):
            self._load(task)
