"""Research API endpoints — start, status, results, and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.dependencies import get_checkpointer, get_neo4j, get_redis, get_registry
from src.api.v1.schemas.research import (
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
    ResearchStatus,
)
from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.models.llm_registry import LLMRegistry
from src.services.checkpoint_service import CheckpointService
from src.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

_JOB_KEY = "argus:job:{}"
_JOB_TTL = 86400 * 7  # 7 days

# In-memory fallback for when Redis is unavailable (tests / local dev without Redis)
_jobs: dict[str, dict[str, Any]] = {}

# Tracks live asyncio tasks for inline (non-Celery) runs so they can be cancelled
_inline_tasks: dict[str, asyncio.Task] = {}


async def _redis_set_job(research_id: str, data: dict) -> None:
    """Write job metadata to Redis. Falls back silently if Redis unavailable."""
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.set(_JOB_KEY.format(research_id), json.dumps(data), ex=_JOB_TTL)
    except Exception as exc:
        logger.warning("redis_job_write_failed", research_id=research_id, error=str(exc))


async def _redis_get_job(research_id: str) -> dict | None:
    """Read job metadata from Redis. Returns None if not found or Redis unavailable."""
    redis = get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(_JOB_KEY.format(research_id))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("redis_job_read_failed", research_id=research_id, error=str(exc))
        return None


@router.post("", response_model=ResearchResponse)
async def start_research(
    request: ResearchRequest,
    registry: LLMRegistry = Depends(get_registry),
    neo4j: Neo4jConnection = Depends(get_neo4j),
) -> ResearchResponse:
    """Start a new research investigation.

    Queues a background task that runs the LangGraph supervisor agent.
    """
    research_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    job_data = {
        "status": "queued",
        "request": request.model_dump(),
        "created_at": now.isoformat(),
        "state": None,
    }
    _jobs[research_id] = job_data
    await _redis_set_job(research_id, {"status": "queued", "created_at": now.isoformat()})

        # Fire background task (Celery integration in Phase 8; inline fallback here)
    try:
        from src.worker import run_research_task

        run_research_task.delay(research_id, request.model_dump())
        logger.info("research_queued_celery", research_id=research_id)
    except Exception:
        # Celery unavailable — run inline as asyncio task
        task = asyncio.create_task(_run_research_inline(research_id, request, registry, neo4j))
        _inline_tasks[research_id] = task
        logger.info("research_queued_inline", research_id=research_id)

    return ResearchResponse(
        research_id=research_id,
        status="queued",
        created_at=now,
    )


async def _run_research_inline(
    research_id: str,
    request: ResearchRequest,
    registry: LLMRegistry,
    neo4j: Neo4jConnection,
) -> None:
    """Run research inline when Celery is unavailable."""
    from src.agent.graph import compile_research_graph
    from src.api.dependencies import get_checkpointer

    settings = get_settings()
    checkpointer = get_checkpointer()

    _jobs[research_id]["status"] = "running"

    try:
        graph = compile_research_graph(settings, registry, neo4j, checkpointer=checkpointer)

        initial_state = {
            "research_id": research_id,
            "target_name": request.target_name,
            "target_context": request.target_context,
            "research_objectives": request.objectives,
            "current_phase": 0,
            "max_phases": request.max_depth,
            "iteration_count": 0,
            "phase_complete": False,
            "supervisor_instructions": "",
            "search_results_analyzed_count": 0,
            "facts_verified_count": 0,
            "search_queries_executed": [],
            "search_results": [],
            "scraped_content": [],
            "urls_visited": set(),
            "extracted_facts": [],
            "entities": [],
            "relationships": [],
            "contradictions": [],
            "verified_facts": [],
            "unverified_claims": [],
            "risk_flags": [],
            "overall_risk_score": None,
            "graph_nodes_created": [],
            "graph_relationships_created": [],
            "final_report": None,
            "total_tokens_used": 0,
            "total_cost_usd": 0.0,
            "errors": [],
            "audit_log": [],
        }

        config = {"configurable": {"thread_id": research_id}, "recursion_limit": 150}
        result = await graph.ainvoke(initial_state, config)
        _jobs[research_id]["state"] = result
        _jobs[research_id]["status"] = "completed"
        logger.info("research_completed", research_id=research_id)

    except asyncio.CancelledError:
        _jobs[research_id]["status"] = "cancelled"
        logger.info("research_cancelled", research_id=research_id)
        raise

    except Exception as exc:
        _jobs[research_id]["status"] = "failed"
        _jobs[research_id]["error"] = str(exc)
        logger.error("research_failed", research_id=research_id, error=str(exc))

    finally:
        _inline_tasks.pop(research_id, None)


@router.get("/{research_id}", response_model=ResearchResult)
async def get_research(research_id: str) -> ResearchResult:
    """Get full research results."""
    # Redis is the source of truth; fall back to in-memory for inline/test runs
    redis_job = await _redis_get_job(research_id)
    mem_job = _jobs.get(research_id)
    job = redis_job or (mem_job and {"status": mem_job["status"], **mem_job})

    if not job:
        raise HTTPException(status_code=404, detail="Research not found")

    req = mem_job.get("request", {}) if mem_job else {}

    return ResearchResult(
        research_id=research_id,
        status=job.get("status", "unknown"),
        target_name=req.get("target_name", ""),
        target_context=req.get("target_context", ""),
        final_report=job.get("final_report"),
        facts_count=job.get("facts_count", 0),
        entities_count=job.get("entities_count", 0),
        risk_flags_count=job.get("risk_flags_count", 0),
        overall_risk_score=job.get("overall_risk_score"),
        audit_log=job.get("audit_log", []),
    )


@router.get("/{research_id}/status", response_model=ResearchStatus)
async def get_research_status(research_id: str) -> ResearchStatus:
    """Get real-time research status.

    Reads lifecycle status from Redis (written by the Celery worker),
    and live graph progress from the LangGraph checkpoint in Redis.
    """
    redis_job = await _redis_get_job(research_id)
    mem_job = _jobs.get(research_id)

    if not redis_job and not mem_job:
        raise HTTPException(status_code=404, detail="Research not found")

    status = (redis_job or mem_job).get("status", "unknown")

    # Read live graph progress from the LangGraph Redis checkpointer
    checkpointer = get_checkpointer()
    graph_state: dict = {}
    if checkpointer and status == "running":
        svc = CheckpointService(checkpointer)
        graph_state = await svc.get_latest_state(research_id) or {}

    return ResearchStatus(
        research_id=research_id,
        status=status,
        current_phase=graph_state.get("current_phase", 0),
        max_phases=graph_state.get("max_phases", 5),
        facts_extracted=len(graph_state.get("extracted_facts", [])),
        entities_discovered=len(graph_state.get("entities", [])),
        verified_facts=len(graph_state.get("verified_facts", [])),
        risk_flags=len(graph_state.get("risk_flags", [])),
        graph_nodes=len(graph_state.get("graph_nodes_created", [])),
        searches_executed=len(graph_state.get("search_queries_executed", [])),
        iteration_count=graph_state.get("iteration_count", 0),
        errors=graph_state.get("errors", []),
    )


@router.delete("/{research_id}/cancel", status_code=200)
async def cancel_research(research_id: str) -> dict:
    """Cancel a queued or running research job.

    For Celery-backed jobs, revokes the task on the worker.
    For inline asyncio jobs, cancels the background task directly.
    Returns a 409 if the job is already in a terminal state.
    """
    redis_job = await _redis_get_job(research_id)
    mem_job = _jobs.get(research_id)

    if not redis_job and not mem_job:
        raise HTTPException(status_code=404, detail="Research not found")

    status = (redis_job or mem_job).get("status", "unknown")
    if status in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job with status '{status}'",
        )

    # --- Celery path ---
    task_id = (redis_job or {}).get("task_id") or (mem_job or {}).get("task_id")
    if task_id:
        try:
            from src.worker import celery_app

            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
            logger.info("celery_task_revoked", research_id=research_id, task_id=task_id)
        except Exception as exc:
            logger.warning("celery_revoke_failed", research_id=research_id, error=str(exc))

    # --- Inline asyncio path ---
    inline_task = _inline_tasks.get(research_id)
    if inline_task and not inline_task.done():
        inline_task.cancel()
        logger.info("inline_task_cancelled", research_id=research_id)

    # Mark cancelled in both stores
    cancelled_data = {**(redis_job or {}), "status": "cancelled"}
    await _redis_set_job(research_id, cancelled_data)
    if mem_job:
        mem_job["status"] = "cancelled"

    return {"research_id": research_id, "status": "cancelled"}


@router.get("/{research_id}/stream")
async def stream_research(research_id: str) -> EventSourceResponse:
    """SSE endpoint for real-time research progress.

    Streams events as the LangGraph supervisor processes each node.
    Falls back to polling the job state when streaming isn't available.
    """

    async def event_generator():
        checkpointer = get_checkpointer()
        checkpoint_svc = CheckpointService(checkpointer) if checkpointer else None

        while True:
            redis_job = await _redis_get_job(research_id)
            mem_job = _jobs.get(research_id)
            job = redis_job or (mem_job and {"status": mem_job.get("status", "unknown")})

            if not job:
                yield {"event": "error", "data": json.dumps({"error": "not_found"})}
                return

            status = job.get("status", "unknown")

            # Pull live graph progress from LangGraph checkpoint while running
            graph_state: dict = {}
            if checkpoint_svc and status == "running":
                graph_state = await checkpoint_svc.get_latest_state(research_id) or {}

            yield {
                "event": "status",
                "data": json.dumps({
                    "status": status,
                    "current_phase": graph_state.get("current_phase", 0),
                    "facts": len(graph_state.get("extracted_facts", [])),
                    "entities": len(graph_state.get("entities", [])),
                    "iteration": graph_state.get("iteration_count", 0),
                }),
            }

            if status in ("completed", "failed"):
                yield {"event": "done", "data": json.dumps({"status": status})}
                return

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())
