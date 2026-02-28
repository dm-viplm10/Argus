"""Evaluation API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from src.api.v1.schemas.evaluation import (
    EvaluationRequest,
    EvaluationResponse,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/evaluate", tags=["evaluation"])

_evaluations: dict[str, dict] = {}


@router.post("", response_model=EvaluationResponse)
async def run_evaluation(request: EvaluationRequest) -> EvaluationResponse:
    """Run evaluation against ground truth for a completed research job."""
    from src.api.v1.research import _jobs
    from src.evaluation.evaluator import run_evaluation as _run_eval

    job = _jobs.get(request.research_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Research not completed yet")

    state = job.get("state", {})
    eval_id = str(uuid.uuid4())

    try:
        metrics, summary = await _run_eval(
            state=state,
            ground_truth_file=request.ground_truth_file,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ground truth file not found")

    result = EvaluationResponse(
        evaluation_id=eval_id,
        research_id=request.research_id,
        metrics=metrics,
        summary=summary,
    )
    _evaluations[eval_id] = result.model_dump()
    return result


@router.get("/{evaluation_id}/results", response_model=EvaluationResponse)
async def get_evaluation(evaluation_id: str) -> EvaluationResponse:
    """Get evaluation results."""
    data = _evaluations.get(evaluation_id)
    if not data:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationResponse(**data)
