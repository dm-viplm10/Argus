"""Request/response models for the evaluation API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationRequest(BaseModel):
    research_id: str
    ground_truth_file: str = Field(
        default="timothy_overturf.json",
        description="Filename in ground_truth directory",
    )


class EvaluationMetrics(BaseModel):
    fact_recall: float = 0.0
    fact_precision: float = 0.0
    entity_coverage: float = 0.0
    relationship_accuracy: float = 0.0
    risk_detection_rate: float = 0.0
    depth_score: float = 0.0
    efficiency: float = 0.0
    source_quality: float = 0.0


class EvaluationResponse(BaseModel):
    evaluation_id: str
    research_id: str
    metrics: EvaluationMetrics
    summary: str = ""
