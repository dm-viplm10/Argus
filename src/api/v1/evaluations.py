"""Evaluation API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_registry
from src.api.v1.schemas.evaluation import (
    EvaluationRequest,
    EvaluationResponse,
)
from src.models.llm_registry import LLMRegistry
from src.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/evaluate", tags=["evaluation"])

_evaluations: dict[str, dict] = {}


@router.post("", response_model=EvaluationResponse)
async def run_evaluation(
    request: EvaluationRequest,
    registry: LLMRegistry = Depends(get_registry),
) -> EvaluationResponse:
    """Run evaluation against ground truth for a completed research job.

    If ``state`` is provided in the request body, it is used directly (e.g. for
    example state or testing). Otherwise state is retrieved by ``research_id``:

    1. **Redis eval checkpoint** ``argus:evalstate:{research_id}`` (primary —
       JSON state written when the run completes, 30-day TTL)
    2. **In-memory** ``_jobs[id]["state"]`` (same process only)
    3. **LangGraph checkpointer** (fallback if eval key was not written)

    When ``use_llm_judge`` is True (default), each metric is scored by an LLM
    (GPT-4.1) in sequence; the response includes per-metric reasoning and a
    full evaluation report.
    """
    from src.api.v1.research import _jobs, _redis_get_job, redis_get_research_state
    from src.evaluation.evaluator import run_evaluation as _run_eval

    state: dict[str, Any] | None = request.state
    research_id = request.research_id or ""

    if state is None:
        # ── Resolve job and state by research_id ─────────────────────────────
        if not research_id:
            raise HTTPException(
                status_code=400,
                detail="Either research_id or state must be provided",
            )
        mem_job = _jobs.get(research_id)
        status: str | None = mem_job.get("status") if mem_job else None

        if status is None:
            redis_job = await _redis_get_job(research_id)
            if redis_job:
                status = redis_job.get("status", "unknown")
            else:
                probe = await redis_get_research_state(research_id)
                if probe:
                    status = "completed"

        if status is None:
            raise HTTPException(status_code=404, detail="Research job not found")

        if status != "completed":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Research is not completed yet (current status: '{status}'). "
                    "Evaluation requires a finished run."
                ),
            )

        # Prefer state from Redis eval checkpoint (argus:evalstate:{research_id}).
        # Fall back to in-memory then LangGraph checkpointer.
        state = await redis_get_research_state(research_id) or {}
        if not state and mem_job and mem_job.get("state"):
            state = mem_job["state"]
        if not state:
            from src.api.dependencies import get_checkpointer
            from src.services.checkpoint_service import CheckpointService

            cp = get_checkpointer()
            if cp:
                svc = CheckpointService(cp)
                state = await svc.get_latest_state(research_id) or {}

        if not state:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Research state is not available for evaluation. "
                    "Ensure the service runs with Redis checkpointing enabled."
                ),
            )

    # Optional research_id from state for inline state case
    if not research_id and isinstance(state, dict):
        research_id = state.get("research_id", "")

    eval_id = str(uuid.uuid4())
    try:
        metrics, summary, evaluation_report = await _run_eval(
            state=state,
            ground_truth_file=request.ground_truth_file,
            use_llm_judge=request.use_llm_judge,
            registry=registry if request.use_llm_judge else None,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ground truth file '{request.ground_truth_file}' not found",
        )

    result = EvaluationResponse(
        evaluation_id=eval_id,
        research_id=research_id,
        metrics=metrics,
        summary=summary,
        evaluation_report=evaluation_report,
    )
    _evaluations[eval_id] = result.model_dump()
    logger.info("evaluation_completed", research_id=research_id, eval_id=eval_id)
    return result


@router.get("/{evaluation_id}/results", response_model=EvaluationResponse)
async def get_evaluation(evaluation_id: str) -> EvaluationResponse:
    """Get evaluation results."""
    data = _evaluations.get(evaluation_id)
    if not data:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationResponse(**data)
