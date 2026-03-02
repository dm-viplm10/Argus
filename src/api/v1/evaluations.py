"""Evaluation API endpoints."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_registry, get_research_service
from src.api.v1.schemas.evaluation import (
    EvaluationRequest,
    EvaluationResponse,
)
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.models.llm_registry import LLMRegistry
    from src.services.research_service import ResearchService

logger = get_logger(__name__)
router = APIRouter(prefix="/evaluate", tags=["evaluation"])

# Bounded in-memory store: oldest entries are evicted when the cap is reached.
# Evaluation results are transient — for long-term storage use Redis or a database.
_MAX_EVALUATIONS = 1_000
_evaluations: OrderedDict[str, dict] = OrderedDict()


@router.post("", response_model=EvaluationResponse)
async def run_evaluation(
    request: EvaluationRequest,
    registry: LLMRegistry = Depends(get_registry),
    svc: ResearchService = Depends(get_research_service),
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
    from src.evaluation.evaluator import run_evaluation as _run_eval

    state: dict[str, Any] | None = request.state
    research_id = request.research_id or ""

    if state is None:
        if not research_id:
            raise HTTPException(
                status_code=400,
                detail="Either research_id or state must be provided",
            )

        status = await svc.get_job_status(research_id)
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

        # ResearchService.get_research_state() tries Redis eval state, then
        # in-memory, then the LangGraph checkpointer — in priority order.
        state = await svc.get_research_state(research_id) or {}
        if not state:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Research state is not available for evaluation. "
                    "Ensure the service runs with Redis checkpointing enabled."
                ),
            )

    # Optional: extract research_id from inline state when not provided in body.
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
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Ground truth file '{request.ground_truth_file}' not found",
        ) from exc

    result = EvaluationResponse(
        evaluation_id=eval_id,
        research_id=research_id,
        metrics=metrics,
        summary=summary,
        evaluation_report=evaluation_report,
    )
    _evaluations[eval_id] = result.model_dump()
    if len(_evaluations) > _MAX_EVALUATIONS:
        _evaluations.popitem(last=False)  # evict oldest entry
    logger.info("evaluation_completed", research_id=research_id, eval_id=eval_id)
    return result


@router.get("/{evaluation_id}/results", response_model=EvaluationResponse)
async def get_evaluation(evaluation_id: str) -> EvaluationResponse:
    """Get evaluation results."""
    data = _evaluations.get(evaluation_id)
    if not data:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationResponse(**data)
