"""Shared utilities for agent nodes.

Provides helpers for common patterns that appear across multiple node
implementations, keeping individual node files focused on their domain logic.

Functions
---------
truncate_json
    Serialise an object to indented JSON and cap at a character limit, used
    to keep large state payloads within model context windows.
extract_tool_call_args
    Pull named argument fields from the first matching tool call in a
    LangGraph message list — the structural pattern shared by verifier and
    search_and_analyze result extraction.
reset_phase_flags
    Return the standard dict of phase-completion booleans all set to False,
    eliminating the copy-pasted reset block in supervisor, planner, and
    phase_strategist.
"""

from __future__ import annotations

import json
from typing import Any


def truncate_json(obj: Any, max_chars: int) -> str:
    """Serialise *obj* to indented JSON, truncated to *max_chars* characters.

    Used to keep large state payloads (facts, flags, relationships) within
    model context windows without truncating individual entries mid-field.

    Args:
        obj: Any JSON-serialisable object.
        max_chars: Maximum number of characters in the returned string.

    Returns:
        A JSON string of at most *max_chars* characters.
    """
    return json.dumps(obj, indent=2)[:max_chars]


def extract_tool_call_args(
    messages: list,
    tool_name: str,
    fields: list[str],
) -> dict[str, Any]:
    """Extract named fields from the first matching tool call in *messages*.

    Iterates the LangGraph message list and returns the ``args`` dict of the
    first tool call whose name matches *tool_name*, filtered to *fields*.
    Returns empty lists for any field that is absent or when the tool call is
    not found at all.

    Args:
        messages: Output message list from a ReAct agent invocation.
        tool_name: The tool call name to search for (e.g. ``"submit_verification"``).
        fields: Field names to extract from the matching tool call's ``args``.

    Returns:
        A dict keyed by *fields*; values are the extracted data or ``[]``.
    """
    for msg in messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            if tc.get("name") == tool_name:
                args = tc.get("args", {})
                return {f: args.get(f, []) for f in fields}
    return {f: [] for f in fields}


def reset_phase_flags(new_phase: int | None = None) -> dict:
    """Return a dict of phase-completion flags all reset to ``False``.

    Passing *new_phase* also includes ``current_phase`` in the returned dict,
    covering the supervisor's phase-advance path in a single call.

    Args:
        new_phase: When provided, adds ``{"current_phase": new_phase}`` to the
            returned dict.

    Returns:
        A dict suitable for spreading into a node's state-update return value::

            return {
                "research_plan": plan_dicts,
                "current_phase": 1,
                **reset_phase_flags(),
                ...
            }
    """
    flags: dict[str, Any] = {
        "phase_complete": False,
        "current_phase_searched": False,
        "current_phase_verified": False,
        "current_phase_risk_assessed": False,
    }
    if new_phase is not None:
        flags["current_phase"] = new_phase
    return flags
