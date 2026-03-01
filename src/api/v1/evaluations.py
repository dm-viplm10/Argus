"""Evaluation API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

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
    """Run evaluation against ground truth for a completed research job.

    State is retrieved via a three-tier lookup:

    1. **In-memory** ``_jobs[id]["state"]`` — fastest; available in the same
       process that ran the research graph.
    2. **Redis eval key** ``argus:evalstate:{id}`` — written on completion with
       a 30-day TTL; survives server restarts.
    3. **LangGraph checkpoint** via ``CheckpointService.get_latest_state()`` —
       written by ``AsyncRedisSaver`` after every node; serves as a fallback if
       the dedicated eval key was somehow not written.
    """
    # Lazy imports avoid circular dependency with research.py at module load time.
    from src.api.v1.research import _jobs, _redis_get_job, redis_get_research_state
    from src.evaluation.evaluator import run_evaluation as _run_eval

    research_id = request.research_id

    # ── Resolve job status ────────────────────────────────────────────────────
    # In-memory is authoritative while the process is alive.
    mem_job = _jobs.get(research_id)
    status: str | None = mem_job.get("status") if mem_job else None

    if status is None:
        # Process restarted — fall back to the Redis job record.
        redis_job = await _redis_get_job(research_id)
        if redis_job:
            status = redis_job.get("status", "unknown")
        else:
            # Last resort: presence of the eval state key implies the run completed.
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

    # ── Retrieve persisted state (3-tier) ────────────────────────────────────
    state: dict[str, Any] = {}

    # Tier 1 — in-memory (same process, zero I/O)
    if mem_job and mem_job.get("state"):
        state = mem_job["state"]

    # Tier 2 — dedicated Redis eval key (written on completion, 30-day TTL)
    if not state:
        state = await redis_get_research_state(research_id) or {}

    # Tier 3 — LangGraph checkpointer (AsyncRedisSaver writes after every node)
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
                "Ensure the service runs with Redis checkpointing enabled "
                "(set REDIS_URL and install langgraph-checkpoint-redis)."
            ),
        )

    # ── Run evaluation ────────────────────────────────────────────────────────
    eval_id = str(uuid.uuid4())
    try:
        metrics, summary = await _run_eval(
            state=state,
            ground_truth_file=request.ground_truth_file,
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
