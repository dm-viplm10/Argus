"""Research API endpoints — start, status, results, and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.dependencies import get_checkpointer, get_neo4j, get_registry
from src.api.v1.schemas.research import (
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
    ResearchStatus,
)
from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.models.llm_registry import LLMRegistry
from src.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

# In-memory job tracking (replaced by Redis/Celery in production)
_jobs: dict[str, dict[str, Any]] = {}


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

    _jobs[research_id] = {
        "status": "queued",
        "request": request.model_dump(),
        "created_at": now.isoformat(),
        "state": None,
    }

    # Fire background task (Celery integration in Phase 8; inline fallback here)
    try:
        from src.worker import run_research_task

        run_research_task.delay(research_id, request.model_dump())
        logger.info("research_queued_celery", research_id=research_id)
    except Exception:
        # Celery unavailable — run inline as asyncio task
        asyncio.create_task(_run_research_inline(research_id, request, registry, neo4j))
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

        config = {"configurable": {"thread_id": research_id}}
        result = await graph.ainvoke(initial_state, config)
        _jobs[research_id]["state"] = result
        _jobs[research_id]["status"] = "completed"
        logger.info("research_completed", research_id=research_id)

    except Exception as exc:
        _jobs[research_id]["status"] = "failed"
        _jobs[research_id]["error"] = str(exc)
        logger.error("research_failed", research_id=research_id, error=str(exc))


@router.get("/{research_id}", response_model=ResearchResult)
async def get_research(research_id: str) -> ResearchResult:
    """Get full research results."""
    job = _jobs.get(research_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research not found")

    state = job.get("state") or {}
    req = job.get("request", {})

    return ResearchResult(
        research_id=research_id,
        status=job["status"],
        target_name=req.get("target_name", ""),
        target_context=req.get("target_context", ""),
        final_report=state.get("final_report"),
        facts_count=len(state.get("verified_facts", [])),
        entities_count=len(state.get("entities", [])),
        risk_flags_count=len(state.get("risk_flags", [])),
        overall_risk_score=state.get("overall_risk_score"),
        audit_log=state.get("audit_log", []),
    )


@router.get("/{research_id}/status", response_model=ResearchStatus)
async def get_research_status(research_id: str) -> ResearchStatus:
    """Get real-time research status."""
    job = _jobs.get(research_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research not found")

    state = job.get("state") or {}

    return ResearchStatus(
        research_id=research_id,
        status=job["status"],
        current_phase=state.get("current_phase", 0),
        max_phases=state.get("max_phases", 5),
        facts_extracted=len(state.get("extracted_facts", [])),
        entities_discovered=len(state.get("entities", [])),
        verified_facts=len(state.get("verified_facts", [])),
        risk_flags=len(state.get("risk_flags", [])),
        graph_nodes=len(state.get("graph_nodes_created", [])),
        searches_executed=len(state.get("search_queries_executed", [])),
        iteration_count=state.get("iteration_count", 0),
        errors=state.get("errors", []),
    )


@router.get("/{research_id}/stream")
async def stream_research(research_id: str) -> EventSourceResponse:
    """SSE endpoint for real-time research progress.

    Streams events as the LangGraph supervisor processes each node.
    Falls back to polling the job state when streaming isn't available.
    """

    async def event_generator():
        while True:
            job = _jobs.get(research_id)
            if not job:
                yield {"event": "error", "data": json.dumps({"error": "not_found"})}
                return

            status = job["status"]
            state = job.get("state") or {}

            yield {
                "event": "status",
                "data": json.dumps({
                    "status": status,
                    "current_phase": state.get("current_phase", 0),
                    "facts": len(state.get("extracted_facts", [])),
                    "entities": len(state.get("entities", [])),
                    "iteration": state.get("iteration_count", 0),
                }),
            }

            if status in ("completed", "failed"):
                yield {"event": "done", "data": json.dumps({"status": status})}
                return

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())
