"""Prompt registry — central access to all agent prompt templates.

Loads templates from .md files in the templates/ subdirectory.
Uses string.Template ($variable) for substitution so markdown files
can contain literal JSON braces without escaping.
"""

from __future__ import annotations

import string
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"


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
