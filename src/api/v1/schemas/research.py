"""Request/response Pydantic models for the research API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    target_name: str = Field(..., min_length=1, examples=["Timothy Overturf"])
    target_context: str = Field(default="", examples=["CEO of Sisu Capital"])
    objectives: list[str] = Field(
        default_factory=lambda: ["biographical", "financial", "risk_assessment", "connections"],
    )
    max_depth: int = Field(default=5, ge=1, le=10)


class ResearchResponse(BaseModel):
    research_id: str
    status: str
    created_at: datetime


class ResearchStatus(BaseModel):
    research_id: str
    status: Literal["queued", "running", "completed", "failed"]
    current_phase: int = 0
    max_phases: int = 5
    facts_extracted: int = 0
    entities_discovered: int = 0
    verified_facts: int = 0
    risk_flags: int = 0
    graph_nodes: int = 0
    searches_executed: int = 0
    iteration_count: int = 0
    errors: list[dict] = Field(default_factory=list)


class ResearchResult(BaseModel):
    research_id: str
    status: str
    target_name: str
    target_context: str
    final_report: str | None = None
    facts_count: int = 0
    entities_count: int = 0
    risk_flags_count: int = 0
    overall_risk_score: float | None = None
    audit_log: list[dict] = Field(default_factory=list)
