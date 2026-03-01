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

# Tracks live asyncio tasks so they can be cancelled
_inline_tasks: dict[str, asyncio.Task] = {}

# Per-job event queues: astream pushes writer() events here, SSE endpoint consumes.
_event_queues: dict[str, asyncio.Queue] = {}

# Shared cancellation set — checked by the supervisor node between every step.
# This enables cooperative cancellation that actually stops the LLM pipeline.
_cancelled_jobs: set[str] = set()

# Node names registered in the StateGraph — used to filter astream_events
# down to graph-level node transitions (ignoring internal sub-chains).
_GRAPH_NODES = frozenset({
    "supervisor", "planner", "phase_strategist", "query_refiner", "search_and_analyze",
    "verifier", "risk_assessor", "graph_builder", "synthesizer",
})


def _to_sse_event(raw: dict) -> tuple[str, dict] | None:
    """Map a LangGraph stream event to a frontend-friendly (event_type, data) pair.

    Returns None for events that should not be forwarded to the client.
    """
    kind = raw["event"]
    node = raw.get("metadata", {}).get("langgraph_node", "")

    if kind == "on_chain_start" and raw.get("name") in _GRAPH_NODES:
        return ("node_start", {"node": raw["name"]})

    if kind == "on_chain_end" and raw.get("name") in _GRAPH_NODES:
        output = raw.get("data", {}).get("output") or {}
        summary: dict[str, Any] = {"node": raw["name"]}
        if isinstance(output, dict):
            for key in ("extracted_facts", "entities", "verified_facts",
                        "risk_flags", "pending_queries"):
                val = output.get(key)
                if isinstance(val, list) and val:
                    summary[key] = len(val)
            if output.get("research_plan"):
                summary["phases"] = len(output["research_plan"])
            if output.get("final_report"):
                summary["has_report"] = True
            if output.get("overall_risk_score") is not None:
                summary["risk_score"] = output["overall_risk_score"]
        return ("node_end", summary)

    if kind == "on_chat_model_stream":
        chunk = raw.get("data", {}).get("chunk")
        if chunk is None:
            return None
        content = getattr(chunk, "content", "")
        # Claude returns content as a list of typed blocks (thinking / text / tool_use)
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "thinking":
                    t = block.get("thinking", "")
                    if t:
                        return ("thinking", {"node": node, "content": t})
                elif block.get("type") == "text":
                    t = block.get("text", "")
                    if t:
                        return ("token", {"node": node, "content": t})
            return None
        if isinstance(content, str) and content:
            return ("token", {"node": node, "content": content})
        return None

    if kind == "on_tool_start":
        tool_input = raw.get("data", {}).get("input")
        return ("tool_start", {
            "node": node,
            "tool": raw.get("name", ""),
            "input": str(tool_input)[:500] if tool_input else "",
        })

    if kind == "on_tool_end":
        output = raw.get("data", {}).get("output", "")
        return ("tool_end", {
            "node": node,
            "tool": raw.get("name", ""),
            "output": str(output)[:500],
        })

    return None


def is_job_cancelled(research_id: str) -> bool:
    """Check if a research job has been marked for cancellation."""
    return research_id in _cancelled_jobs


def clear_cancellation(research_id: str) -> None:
    """Remove a job from the cancellation set after cleanup."""
    _cancelled_jobs.discard(research_id)


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

    # Run inline as asyncio task — required for SSE streaming.
    _event_queues[research_id] = asyncio.Queue()
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
    """Run research graph in the background with SSE streaming."""
    from src.agent.graph import compile_research_graph
    from src.api.dependencies import get_checkpointer

    settings = get_settings()
    checkpointer = get_checkpointer()
    queue = _event_queues.get(research_id)

    _jobs[research_id]["status"] = "running"

    try:
        graph = compile_research_graph(settings, registry, neo4j, checkpointer=checkpointer)

        # When max_depth is omitted, use dynamic phase strategy: start with Phase 1 only,
        # then phase_strategist decides additional phases based on surface report findings.
        use_dynamic_phases = request.max_depth is None
        max_phases = 1 if use_dynamic_phases else request.max_depth

        initial_state = {
            "research_id": research_id,
            "target_name": request.target_name,
            "target_context": request.target_context,
            "research_objectives": request.objectives,
            "current_phase": 0,
            "max_phases": max_phases,
            "dynamic_phases": use_dynamic_phases,
            "iteration_count": 0,
            "phase_complete": False,
            "supervisor_instructions": "",
            "current_phase_searched": False,
            "current_phase_verified": False,
            "current_phase_risk_assessed": False,
            "facts_verified_count": 0,
            "risk_assessed_facts_count": 0,
            "search_queries_executed": [],
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

        async for raw in graph.astream_events(
            initial_state,
            config,
            version="v2",
            include_types=["chain", "chat_model", "tool"],
        ):
            if queue is not None:
                sse = _to_sse_event(raw)
                if sse is not None:
                    await queue.put(sse)

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
        if queue is not None:
            await queue.put(None)  # sentinel: tells SSE consumer the stream is done
        _inline_tasks.pop(research_id, None)
        clear_cancellation(research_id)


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

    Reads lifecycle status and live graph progress from the LangGraph checkpoint in Redis.
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

    audit_log = graph_state.get("audit_log", [])
    current_node = audit_log[-1].get("node") if audit_log else None

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
        current_node=current_node,
        audit_log=audit_log,
    )


@router.delete("/{research_id}/cancel", status_code=200)
async def cancel_research(research_id: str) -> dict:
    """Cancel a queued or running research job.

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

    # Signal cooperative cancellation so the supervisor stops routing
    _cancelled_jobs.add(research_id)
    logger.info("cancellation_signalled", research_id=research_id)

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
    """SSE endpoint — pipes graph node events to the client in real time.

    Each `event: node` carries the exact dict emitted by get_stream_writer()
    inside each LangGraph node (supervisor, planner, analyzer, etc.).
    A final `event: done` signals the stream is over.
    """

    async def event_generator():
        queue = _event_queues.get(research_id)

        if queue is None:
            # No live stream: run finished / not inline / not found
            job = _jobs.get(research_id)
            if job:
                yield {"event": "done", "data": json.dumps({"status": job.get("status", "unknown")})}
            else:
                yield {"event": "error", "data": json.dumps({"error": "not_found"})}
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue

                if event is None:
                    status = _jobs.get(research_id, {}).get("status", "completed")
                    yield {"event": "done", "data": json.dumps({"status": status})}
                    return

                event_type, data = event
                yield {"event": event_type, "data": json.dumps(data)}
        finally:
            _event_queues.pop(research_id, None)

    return EventSourceResponse(event_generator())
