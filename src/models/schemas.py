"""Internal Pydantic models for structured data flowing through the agent pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Extracted data models ────────────────────────────────────────────


class ExtractedFact(BaseModel):
    fact: str
    category: Literal["biographical", "professional", "financial", "legal", "social", "behavioral"]
    confidence: float = Field(default=0.5, description="0.0 to 1.0")
    source_url: str
    source_type: Literal["official", "news", "social", "forum", "filing", "unknown"] = "unknown"
    date_mentioned: str | None = None
    entities_involved: list[str] = Field(default_factory=list)


class EntityAttributes(BaseModel):
    """Common attributes for entities (Gemini-compatible strict schema)."""
    model_config = ConfigDict(extra="forbid")
    
    role: str = ""
    title: str = ""
    position: str = ""
    location: str = ""
    founded: str = ""
    url: str = ""
    industry: str = ""
    description: str = ""
    date: str = ""
    value: str = ""


class ExtractedEntity(BaseModel):
    name: str
    type: Literal["person", "organization", "fund", "location", "event", "document"]
    attributes: EntityAttributes = Field(default_factory=EntityAttributes)
    sources: list[str] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    source_entity: str
    target_entity: str
    relationship_type: str
    evidence: str = ""
    confidence: float = Field(default=0.5, description="0.0 to 1.0")
    source_url: str = ""


# ── Risk models ──────────────────────────────────────────────────────


class RiskFlag(BaseModel):
    flag: str
    category: Literal["legal", "financial", "reputational", "behavioral", "network"]
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(default=0.5, description="0.0 to 1.0")
    evidence: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    recommended_followup: str = ""


# ── Planning models ──────────────────────────────────────────────────


class ResearchPhase(BaseModel):
    phase_number: int
    name: str
    description: str
    queries: list[str] = Field(default_factory=list)
    expected_info_types: list[str] = Field(default_factory=list)
    priority: int = Field(default=3)  # 1=highest, 5=lowest; Anthropic doesn't support min/max on integer schemas


class ResearchPlan(BaseModel):
    phases: list[ResearchPhase]
    total_estimated_queries: int = 0
    rationale: str = ""


# ── Verification models ─────────────────────────────────────────────


class VerifiedFact(BaseModel):
    fact: str
    category: str
    final_confidence: float = Field(default=0.5, description="0.0 to 1.0")
    supporting_sources: list[str] = Field(default_factory=list)
    contradicting_sources: list[str] = Field(default_factory=list)
    notes: str = ""


class Contradiction(BaseModel):
    claim_a: str
    claim_b: str
    source_a: str
    source_b: str
    resolution: str = ""


# ── Analyzer output ──────────────────────────────────────────────────


class AnalyzerOutput(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


# ── Verifier output ──────────────────────────────────────────────────


class VerifierOutput(BaseModel):
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)


# ── Risk assessor output ─────────────────────────────────────────────


class RiskAssessment(BaseModel):
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    overall_risk_score: float = Field(default=0.0, description="0.0 to 1.0")
    summary: str = ""


# ── Query refiner output ─────────────────────────────────────────────


class RefinedQueries(BaseModel):
    queries: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ── Supervisor decision ──────────────────────────────────────────────


class SupervisorDecision(BaseModel):
    next_agent: str
    reasoning: str = ""
    instructions_for_agent: str = ""


# ── Audit ────────────────────────────────────────────────────────────


class AuditEntry(BaseModel):
    node: str
    action: str
    timestamp: str
    model_used: str = ""
    input_summary: str = ""
    output_summary: str = ""
    tokens_consumed: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
