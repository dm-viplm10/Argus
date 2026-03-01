"""Request/response models for the evaluation API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationRequest(BaseModel):
    research_id: str = Field(
        default="",
        description="Research job ID to evaluate (optional if state is provided)",
    )
    ground_truth_file: str = Field(
        default="timothy_overturf.json",
        description="Filename in ground_truth directory",
    )
    state: dict | None = Field(
        default=None,
        description="Optional inline state (e.g. from a completed run). If set, research_id is not required.",
    )
    use_llm_judge: bool = Field(
        default=True,
        description="When True, score each metric using LLM-as-judge (GPT-4.1) in sequence.",
    )


class MetricScore(BaseModel):
    """Per-metric score and reasoning from LLM judge."""

    score: float = 0.0
    reasoning: str = ""


class EvaluationMetrics(BaseModel):
    fact_precision: float = Field(
        0.0,
        description="Verified facts / (verified_facts + unverified_claims) from state; computed from state data only.",
    )
    network_fidelity: float = Field(
        0.0,
        description="Semantic similarity and importance of discovered entities and relationships (merged from entity/relationship coverage).",
    )
    risk_detection_rate: float = 0.0
    depth_score: float = 0.0
    efficiency: float = 0.0
    source_quality: float = 0.0
    metric_reasoning: dict[str, str] = Field(
        default_factory=dict,
        description="Per-metric reasoning from LLM judge when use_llm_judge=True",
    )


class EvaluationResponse(BaseModel):
    evaluation_id: str
    research_id: str = ""
    metrics: EvaluationMetrics
    summary: str = ""
    evaluation_report: str = Field(
        default="",
        description="Full evaluation report (LLM judge summary + per-metric reasoning).",
    )
