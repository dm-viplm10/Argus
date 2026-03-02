"""In-process cooperative cancellation registry for research jobs.

Decoupled from the API layer — the API writes cancellation signals here,
and the supervisor reads from here. Neither layer imports from the other.

This is intentionally a simple module-level set: research jobs are
process-local asyncio tasks, so a process-local registry is correct.
"""

from __future__ import annotations

_cancelled_jobs: set[str] = set()


def mark_cancelled(research_id: str) -> None:
    """Signal that a research job should stop at the next supervisor step."""
    _cancelled_jobs.add(research_id)


def is_cancelled(research_id: str) -> bool:
    """Return True if the job has been marked for cancellation."""
    return research_id in _cancelled_jobs


def clear(research_id: str) -> None:
    """Remove the cancellation signal after cleanup. Safe to call multiple times."""
    _cancelled_jobs.discard(research_id)
