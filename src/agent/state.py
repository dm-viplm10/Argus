"""Research agent state schema for the LangGraph supervisor graph."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


def _merge_lists(left: list, right: list) -> list:
    """Append new items to an existing list."""
    return left + right


def _merge_sets(left: set, right: set) -> set:
    """Union two sets."""
    return left | right


class ResearchState(TypedDict, total=False):
    """Full state schema for the research supervisor graph.

    Fields use Annotated reducers so that node outputs are merged
    into the cumulative state rather than overwriting it.
    """

    # ── Input (set once at start) ──
    research_id: str
    target_name: str
    target_context: str
    research_objectives: list[str]

    # ── Supervisor control ──
    current_agent: str
    next_action: str
    supervisor_instructions: str   # Contextual guidance from supervisor to the next node
    research_plan: list[dict]
    current_phase: int
    max_phases: int
    phase_complete: bool
    pending_queries: list[str]

    # ── Per-phase progress flags (reset to False on every phase advance) ──
    # Each flag is set to True by the corresponding node and cleared by the
    # supervisor when it increments current_phase. This gives the supervisor
    # accurate per-phase routing signals instead of relying on global counts.
    # Note: current_phase_searched implies both searched AND analyzed —
    # search_and_analyze sets it True once queries are processed and
    # findings are extracted in the same ReAct pass.
    current_phase_searched: bool
    current_phase_verified: bool
    current_phase_risk_assessed: bool

    # ── Delta processing cursors (never reset; only advance) ──
    # Prevents nodes from re-processing already-handled data on subsequent phases
    facts_verified_count: int               # How many extracted_facts the verifier has processed
    risk_assessed_facts_count: int          # How many verified_facts the risk_assessor has processed

    # ── Search ──
    search_queries_executed: Annotated[list[dict], _merge_lists]
    urls_visited: Annotated[set[str], _merge_sets]

    # ── Analysis (written directly by search_and_analyze) ──
    extracted_facts: Annotated[list[dict], _merge_lists]
    entities: Annotated[list[dict], _merge_lists]
    relationships: Annotated[list[dict], _merge_lists]
    contradictions: Annotated[list[dict], _merge_lists]

    # ── Verification ──
    verified_facts: Annotated[list[dict], _merge_lists]
    unverified_claims: Annotated[list[str], _merge_lists]

    # ── Risk ──
    risk_flags: Annotated[list[dict], _merge_lists]
    overall_risk_score: float | None

    # ── Graph DB ──
    graph_nodes_created: Annotated[list[str], _merge_lists]
    graph_relationships_created: Annotated[list[str], _merge_lists]

    # ── Output ──
    final_report: str | None

    # ── Meta & audit ──
    iteration_count: int
    total_tokens_used: int
    total_cost_usd: float
    errors: Annotated[list[dict], _merge_lists]
    audit_log: Annotated[list[dict], _merge_lists]
