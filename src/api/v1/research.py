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

# Dedicated Redis key for the eval-ready state snapshot (written once on completion).
_STATE_KEY = "argus:evalstate:{}"
_STATE_TTL = 86400 * 30  # 30 days — eval data must outlive the research run TTL

# ResearchState fields required by all 8 evaluation metrics.
# Excludes large/transient fields (urls_visited, pending_queries, internal cursors).
_EVAL_STATE_FIELDS = frozenset({
    "research_id", "target_name", "target_context",
    "verified_facts",           # → fact_recall, fact_precision, depth_score, source_quality
    "entities",                 # → entity_coverage
    "relationships",            # → relationship_accuracy
    "risk_flags",               # → risk_detection_rate
    "search_queries_executed",  # → efficiency
    "extracted_facts", "contradictions", "unverified_claims",
    "overall_risk_score", "final_report",
    "audit_log", "errors",
    "total_tokens_used", "total_cost_usd",
    "iteration_count", "current_phase", "max_phases",
    "graph_nodes_created", "graph_relationships_created",
})

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


# ── Eval state persistence helpers ───────────────────────────────────────────

def _serialize_state_for_eval(state: dict) -> dict:
    """Extract eval-relevant fields and make the dict JSON-safe.

    ResearchState contains ``urls_visited: set[str]`` which is not JSON-serializable.
    All other annotated list fields are already lists after LangGraph reducer merging.
    """
    result: dict[str, Any] = {}
    for field in _EVAL_STATE_FIELDS:
        val = state.get(field)
        if val is None:
            continue
        result[field] = list(val) if isinstance(val, set) else val
    return result


async def _redis_set_research_state(research_id: str, state: dict) -> None:
    """Persist the serialized eval state to Redis under a dedicated key.

    Written once when the research run completes. TTL is 30 days so the
    evaluation endpoint can retrieve the state even after a server restart.
    """
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.set(
            _STATE_KEY.format(research_id),
            json.dumps(state, default=str),  # default=str handles any residual non-serializable
            ex=_STATE_TTL,
        )
        logger.info(
            "eval_state_persisted",
            research_id=research_id,
            verified_facts=len(state.get("verified_facts", [])),
            entities=len(state.get("entities", [])),
            risk_flags=len(state.get("risk_flags", [])),
        )
    except Exception as exc:
        logger.warning("redis_state_write_failed", research_id=research_id, error=str(exc))


async def redis_get_research_state(research_id: str) -> dict | None:
    """Read the persisted eval state from Redis.

    Public (no leading underscore) so the evaluation endpoint can import it.
    Returns ``None`` if the key does not exist or Redis is unavailable.
    """
    redis = get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(_STATE_KEY.format(research_id))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("redis_state_read_failed", research_id=research_id, error=str(exc))
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

        # Two configs: full config for graph execution (recursion_limit controls the
        # LangGraph step counter); minimal config for checkpoint reads (aget_state /
        # checkpointer.aget only need thread_id — passing extra keys like
        # recursion_limit can confuse the checkpoint key lookup in some
        # langgraph-checkpoint-redis versions).
        config = {"configurable": {"thread_id": research_id}, "recursion_limit": 150}
        _ckpt_config = {"configurable": {"thread_id": research_id}}

        # State keys that are present in a real ResearchState but absent from intermediate
        # chain events — used to identify the root graph on_chain_end event below.
        _STATE_INDICATOR_KEYS = frozenset({
            "verified_facts", "entities", "risk_flags",
            "extracted_facts", "relationships", "audit_log",
        })
        _event_captured_state: dict[str, Any] = {}

        async for raw in graph.astream_events(
            initial_state,
            config,
            version="v2",
            include_types=["chain", "chat_model", "tool"],
        ):
            # ── SSE forwarding ────────────────────────────────────────────────
            if queue is not None:
                sse = _to_sse_event(raw)
                if sse is not None:
                    await queue.put(sse)

            # ── Opportunistic state capture from the root graph completion ───
            # When the StateGraph reaches END, astream_events emits a final
            # on_chain_end for the root LangGraph chain (name NOT in _GRAPH_NODES)
            # whose data.output is the fully-reduced ResearchState.  We capture it
            # here so state is always available even if the Redis checkpointer fails.
            if (
                raw.get("event") == "on_chain_end"
                and raw.get("name") not in _GRAPH_NODES
                and raw.get("name") not in {"", "__start__"}
            ):
                output = raw.get("data", {}).get("output")
                if isinstance(output, dict) and output.keys() & _STATE_INDICATOR_KEYS:
                    _event_captured_state = output
                    logger.debug(
                        "state_observed_in_root_event",
                        research_id=research_id,
                        chain_name=raw.get("name"),
                    )

        # ── Three-path state capture (priority order) ─────────────────────────
        # Path 1 and 2 read from the Redis checkpointer (AsyncRedisSaver writes
        # after every node).  Path 3 uses what we captured from the event stream
        # above and works even without a checkpointer.
        final_state: dict[str, Any] = {}
        try:
            if checkpointer is not None:
                # Path 1 — graph.aget_state() with a clean, minimal config
                snapshot = await graph.aget_state(_ckpt_config)
                if snapshot and snapshot.values:
                    final_state = _serialize_state_for_eval(snapshot.values)
                    logger.info(
                        "state_captured_via_aget_state",
                        research_id=research_id,
                        verified_facts=len(final_state.get("verified_facts", [])),
                    )
                else:
                    logger.warning(
                        "aget_state_returned_empty",
                        research_id=research_id,
                        snapshot_none=snapshot is None,
                        values_empty=snapshot is not None and not snapshot.values,
                    )

                # Path 2 — direct checkpointer.aget() (same Redis data, different API path)
                if not final_state:
                    raw_checkpoint = await checkpointer.aget(_ckpt_config)
                    if raw_checkpoint and "channel_values" in raw_checkpoint:
                        final_state = _serialize_state_for_eval(raw_checkpoint["channel_values"])
                        logger.info(
                            "state_captured_via_direct_checkpoint",
                            research_id=research_id,
                            verified_facts=len(final_state.get("verified_facts", [])),
                        )
                    else:
                        logger.warning(
                            "direct_checkpoint_also_empty",
                            research_id=research_id,
                            raw_checkpoint_none=raw_checkpoint is None,
                        )
            else:
                logger.warning(
                    "no_checkpointer_eval_state_unavailable",
                    research_id=research_id,
                    hint="Install langgraph-checkpoint-redis and set REDIS_URL",
                )

            # Path 3 — state observed in the root on_chain_end event during streaming
            # This path is completely independent of the checkpointer.
            if not final_state and _event_captured_state:
                final_state = _serialize_state_for_eval(_event_captured_state)
                logger.info(
                    "state_captured_from_stream_event",
                    research_id=research_id,
                    verified_facts=len(final_state.get("verified_facts", [])),
                )

            if not final_state:
                logger.error(
                    "all_state_capture_paths_failed",
                    research_id=research_id,
                    checkpointer_set=checkpointer is not None,
                    event_state_keys=list(_event_captured_state.keys()) if _event_captured_state else [],
                )

        except Exception as exc:
            # State capture failure must never block the completed status update.
            logger.warning(
                "final_state_capture_failed",
                research_id=research_id,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        # ── Update in-memory job record ───────────────────────────────────────
        _jobs[research_id].update({
            "status": "completed",
            "state": final_state,
            "final_report": final_state.get("final_report"),
            "facts_count": len(final_state.get("verified_facts", [])),
            "entities_count": len(final_state.get("entities", [])),
            "risk_flags_count": len(final_state.get("risk_flags", [])),
            "overall_risk_score": final_state.get("overall_risk_score"),
            "audit_log": final_state.get("audit_log", []),
        })

        # ── Persist to Redis (survives process restarts) ──────────────────────
        await _redis_set_research_state(research_id, final_state)
        await _redis_set_job(research_id, {
            "status": "completed",
            "final_report": final_state.get("final_report"),
            "facts_count": len(final_state.get("verified_facts", [])),
            "entities_count": len(final_state.get("entities", [])),
            "risk_flags_count": len(final_state.get("risk_flags", [])),
            "overall_risk_score": final_state.get("overall_risk_score"),
        })

        logger.info(
            "research_completed",
            research_id=research_id,
            verified_facts=len(final_state.get("verified_facts", [])),
            entities=len(final_state.get("entities", [])),
            risk_flags=len(final_state.get("risk_flags", [])),
        )

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
