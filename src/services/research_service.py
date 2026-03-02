"""Research job lifecycle service.

Owns all in-process job state (_jobs, _inline_tasks, _event_queues) and all
Redis persistence for job metadata and evaluation state. The API endpoints
delegate to this service; they do not hold any job state themselves.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.agent.cancellation import clear, mark_cancelled
from src.services.checkpoint_service import CheckpointService
from src.utils.logging import get_logger

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from src.api.v1.schemas.research import ResearchRequest
    from src.config import Settings
    from src.graph_db.connection import Neo4jConnection
    from src.models.llm_registry import LLMRegistry

logger = get_logger(__name__)

# ── Redis key templates & TTLs ────────────────────────────────────────────────

_JOB_KEY = "argus:job:{}"
_JOB_TTL = 86400 * 7  # 7 days

_STATE_KEY = "argus:evalstate:{}"
_STATE_TTL = 86400 * 30  # 30 days — eval state must outlive job TTL

# ResearchState fields required by all evaluation metrics.
# Excludes large / transient fields (urls_visited, pending_queries, cursors).
_EVAL_STATE_FIELDS: frozenset[str] = frozenset({
    "research_id", "target_name", "target_context",
    "verified_facts",
    "entities",
    "relationships",
    "risk_flags",
    "search_queries_executed",
    "extracted_facts", "contradictions", "unverified_claims",
    "overall_risk_score", "final_report",
    "audit_log", "errors",
    "total_tokens_used", "total_cost_usd",
    "iteration_count", "current_phase", "max_phases",
    "graph_nodes_created", "graph_relationships_created",
})

# Keys that identify the root graph on_chain_end event during streaming.
_STATE_INDICATOR_KEYS: frozenset[str] = frozenset({
    "verified_facts", "entities", "risk_flags",
    "extracted_facts", "relationships", "audit_log",
})


class ResearchService:
    """Orchestrates research job creation, streaming, status tracking, and eval state."""

    def __init__(
        self,
        settings: Settings,
        registry: LLMRegistry,
        neo4j_conn: Neo4jConnection,
        redis_client: aioredis.Redis | None,
        checkpointer: Any | None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._neo4j = neo4j_conn
        self._redis = redis_client
        self._checkpointer = checkpointer

        # In-process state (process-local, not shared across replicas)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._inline_tasks: dict[str, asyncio.Task] = {}
        self._event_queues: dict[str, asyncio.Queue] = {}

    # ── Redis helpers ─────────────────────────────────────────────────────────

    async def _redis_set_job(self, research_id: str, data: dict) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(_JOB_KEY.format(research_id), json.dumps(data), ex=_JOB_TTL)
        except Exception as exc:
            logger.warning("redis_job_write_failed", research_id=research_id, error=str(exc))

    async def _redis_get_job(self, research_id: str) -> dict | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_JOB_KEY.format(research_id))
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("redis_job_read_failed", research_id=research_id, error=str(exc))
            return None

    async def _redis_set_research_state(self, research_id: str, state: dict) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _STATE_KEY.format(research_id),
                json.dumps(state, default=str),
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

    # ── Public helpers (used by evaluations endpoint) ─────────────────────────

    async def get_job_status(self, research_id: str) -> str | None:
        """Return the status string for a job, or None if not found."""
        mem_job = self._jobs.get(research_id)
        if mem_job:
            return mem_job.get("status")
        redis_job = await self._redis_get_job(research_id)
        if redis_job:
            return redis_job.get("status", "unknown")
        # Probe: eval state key existing implies the run completed
        if self._redis is not None:
            try:
                probe = await self._redis.exists(_STATE_KEY.format(research_id))
                if probe:
                    return "completed"
            except Exception:
                pass
        return None

    async def get_research_state(self, research_id: str) -> dict | None:
        """Return the persisted eval state, or None if unavailable."""
        # Priority 1: Redis eval state (written on completion, 30-day TTL)
        if self._redis is not None:
            try:
                raw = await self._redis.get(_STATE_KEY.format(research_id))
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("redis_state_read_failed", research_id=research_id, error=str(exc))

        # Priority 2: in-memory state (same process only, lost on restart)
        mem_job = self._jobs.get(research_id)
        if mem_job and mem_job.get("state"):
            return mem_job["state"]  # type: ignore[return-value]

        # Priority 3: LangGraph checkpointer
        if self._checkpointer is not None:
            cp_svc = CheckpointService(self._checkpointer)
            return await cp_svc.get_latest_state(research_id)

        return None

    def get_event_queue(self, research_id: str) -> asyncio.Queue | None:
        """Return the live raw-event queue for a running job, or None."""
        return self._event_queues.get(research_id)

    def release_event_queue(self, research_id: str) -> None:
        """Remove the event queue slot after the SSE consumer disconnects."""
        self._event_queues.pop(research_id, None)

    # ── State serialization ───────────────────────────────────────────────────

    @staticmethod
    def _serialize_state_for_eval(state: dict) -> dict:
        """Extract eval-relevant fields and make the dict JSON-safe."""
        result: dict[str, Any] = {}
        for field in _EVAL_STATE_FIELDS:
            val = state.get(field)
            if val is None:
                continue
            result[field] = list(val) if isinstance(val, set) else val
        return result

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_job(self, request: ResearchRequest) -> dict[str, Any]:
        """Initialise a new research job and start it as a background asyncio task."""
        research_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        job_data: dict[str, Any] = {
            "status": "queued",
            "request": request.model_dump(),
            "created_at": now.isoformat(),
            "state": None,
        }
        self._jobs[research_id] = job_data
        await self._redis_set_job(research_id, {"status": "queued", "created_at": now.isoformat()})

        self._event_queues[research_id] = asyncio.Queue()
        task = asyncio.create_task(self._run_job(research_id, request))
        self._inline_tasks[research_id] = task
        logger.info("research_queued", research_id=research_id)

        return {"research_id": research_id, "status": "queued", "created_at": now}

    async def get_job_result(self, research_id: str) -> dict[str, Any] | None:
        """Return full job result data, or None if the job does not exist."""
        redis_job = await self._redis_get_job(research_id)
        mem_job = self._jobs.get(research_id)

        if not redis_job and not mem_job:
            return None

        job = redis_job or {"status": mem_job["status"]}  # type: ignore[index]
        req = mem_job.get("request", {}) if mem_job else {}

        return {
            "research_id": research_id,
            "status": job.get("status", "unknown"),
            "target_name": req.get("target_name", ""),
            "target_context": req.get("target_context", ""),
            "final_report": job.get("final_report"),
            "facts_count": job.get("facts_count", 0),
            "entities_count": job.get("entities_count", 0),
            "risk_flags_count": job.get("risk_flags_count", 0),
            "overall_risk_score": job.get("overall_risk_score"),
            "audit_log": job.get("audit_log", []),
        }

    async def get_status(self, research_id: str) -> dict[str, Any] | None:
        """Return live status data, or None if the job does not exist."""
        redis_job = await self._redis_get_job(research_id)
        mem_job = self._jobs.get(research_id)

        if not redis_job and not mem_job:
            return None

        status = (redis_job or mem_job).get("status", "unknown")

        graph_state: dict = {}
        if self._checkpointer and status == "running":
            cp_svc = CheckpointService(self._checkpointer)
            graph_state = await cp_svc.get_latest_state(research_id) or {}

        audit_log = graph_state.get("audit_log", [])
        current_node = audit_log[-1].get("node") if audit_log else None

        return {
            "research_id": research_id,
            "status": status,
            "current_phase": graph_state.get("current_phase", 0),
            "max_phases": graph_state.get("max_phases", 5),
            "facts_extracted": len(graph_state.get("extracted_facts", [])),
            "entities_discovered": len(graph_state.get("entities", [])),
            "verified_facts": len(graph_state.get("verified_facts", [])),
            "risk_flags": len(graph_state.get("risk_flags", [])),
            "graph_nodes": len(graph_state.get("graph_nodes_created", [])),
            "searches_executed": len(graph_state.get("search_queries_executed", [])),
            "iteration_count": graph_state.get("iteration_count", 0),
            "errors": graph_state.get("errors", []),
            "current_node": current_node,
            "audit_log": audit_log,
        }

    async def cancel_job(self, research_id: str) -> dict[str, Any] | None:
        """Cancel a running or queued job. Returns None if not found."""
        redis_job = await self._redis_get_job(research_id)
        mem_job = self._jobs.get(research_id)

        if not redis_job and not mem_job:
            return None

        status = (redis_job or mem_job).get("status", "unknown")
        if status in ("completed", "failed", "cancelled"):
            return {"research_id": research_id, "status": status, "already_terminal": True}

        # Signal cooperative cancellation (supervisor checks this between every step)
        mark_cancelled(research_id)
        logger.info("cancellation_signalled", research_id=research_id)

        # Also hard-cancel the asyncio task for immediate effect
        inline_task = self._inline_tasks.get(research_id)
        if inline_task and not inline_task.done():
            inline_task.cancel()
            logger.info("inline_task_cancelled", research_id=research_id)

        cancelled_data = {**(redis_job or {}), "status": "cancelled"}
        await self._redis_set_job(research_id, cancelled_data)
        if mem_job:
            mem_job["status"] = "cancelled"

        return {"research_id": research_id, "status": "cancelled"}

    # ── Background graph runner ───────────────────────────────────────────────

    async def _run_job(self, research_id: str, request: ResearchRequest) -> None:
        """Run the research graph as a background task with live event streaming.

        Raw LangGraph events are pushed into the job's event queue. The SSE
        endpoint pulls them out and maps them to the client-facing format via
        src.api.v1.sse_mapper — keeping SSE formatting out of this service.

        A hard wall-clock timeout (``RESEARCH_TIMEOUT_SECONDS``) is applied via
        ``asyncio.wait_for`` around ``_execute_graph`` so a runaway LLM loop
        cannot consume unbounded API budget.
        """
        queue = self._event_queues.get(research_id)
        self._jobs[research_id]["status"] = "running"

        try:
            await asyncio.wait_for(
                self._execute_graph(research_id, request, queue),
                timeout=self._settings.RESEARCH_TIMEOUT_SECONDS,
            )

        except TimeoutError:
            self._jobs[research_id].update({
                "status": "failed",
                "error": f"timed out after {self._settings.RESEARCH_TIMEOUT_SECONDS}s",
            })
            await self._redis_set_job(
                research_id,
                {
                    "status": "failed",
                    "error": f"timed out after {self._settings.RESEARCH_TIMEOUT_SECONDS}s",
                },
            )
            logger.error(
                "research_timeout",
                research_id=research_id,
                timeout_seconds=self._settings.RESEARCH_TIMEOUT_SECONDS,
            )

        except asyncio.CancelledError:
            self._jobs[research_id]["status"] = "cancelled"
            logger.info("research_cancelled", research_id=research_id)
            raise

        except Exception as exc:
            self._jobs[research_id]["status"] = "failed"
            self._jobs[research_id]["error"] = str(exc)
            logger.error("research_failed", research_id=research_id, error=str(exc))

        finally:
            if queue is not None:
                await queue.put(None)  # sentinel: tells the SSE consumer the stream is done
            self._inline_tasks.pop(research_id, None)
            clear(research_id)

    async def _execute_graph(
        self,
        research_id: str,
        request: ResearchRequest,
        queue: asyncio.Queue | None,
    ) -> None:
        """Execute the LangGraph pipeline, capture final state, and persist results.

        Extracted from ``_run_job`` so ``asyncio.wait_for`` can apply a hard
        timeout to the entire execution — including streaming, state capture,
        and Redis writes — without duplicating the cleanup logic in the
        ``finally`` block of ``_run_job``.
        """
        from src.agent.graph import compile_research_graph

        graph = compile_research_graph(
            self._settings, self._registry, self._neo4j,
            checkpointer=self._checkpointer,
        )

        use_dynamic_phases = request.max_depth is None
        max_phases = 1 if use_dynamic_phases else request.max_depth

        initial_state: dict[str, Any] = {
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

        # Full config for graph execution; minimal config for checkpoint reads.
        config = {"configurable": {"thread_id": research_id}, "recursion_limit": 150}
        ckpt_config = {"configurable": {"thread_id": research_id}}

        event_captured_state: dict[str, Any] = {}

        async for raw in graph.astream_events(
            initial_state,
            config,
            version="v2",
            include_types=["chain", "chat_model", "tool"],
        ):
            # Push raw event for the SSE endpoint to map and forward
            if queue is not None:
                await queue.put(raw)

            # Capture final state from the root graph completion event.
            # The root event's name is NOT in _STATE_INDICATOR_KEYS (those are
            # node-level keys); but its output dict CONTAINS those keys.
            if (
                raw.get("event") == "on_chain_end"
                and raw.get("name") not in {"", "__start__"}
            ):
                output = raw.get("data", {}).get("output")
                if isinstance(output, dict) and output.keys() & _STATE_INDICATOR_KEYS:
                    event_captured_state = output
                    logger.debug(
                        "state_observed_in_root_event",
                        research_id=research_id,
                        chain_name=raw.get("name"),
                    )

        # ── Three-path state capture (priority order) ─────────────────────
        final_state: dict[str, Any] = {}
        try:
            if self._checkpointer is not None:
                snapshot = await graph.aget_state(ckpt_config)
                if snapshot and snapshot.values:
                    final_state = self._serialize_state_for_eval(snapshot.values)
                    logger.info(
                        "state_captured_via_aget_state",
                        research_id=research_id,
                        verified_facts=len(final_state.get("verified_facts", [])),
                    )
                else:
                    logger.warning("aget_state_returned_empty", research_id=research_id)

                if not final_state:
                    raw_ckpt = await self._checkpointer.aget(ckpt_config)
                    if raw_ckpt and "channel_values" in raw_ckpt:
                        final_state = self._serialize_state_for_eval(
                            raw_ckpt["channel_values"]
                        )
                        logger.info(
                            "state_captured_via_direct_checkpoint",
                            research_id=research_id,
                            verified_facts=len(final_state.get("verified_facts", [])),
                        )
                    else:
                        logger.warning(
                            "direct_checkpoint_also_empty", research_id=research_id
                        )
            else:
                logger.warning(
                    "no_checkpointer_eval_state_unavailable",
                    research_id=research_id,
                    hint="Install langgraph-checkpoint-redis and set REDIS_URL",
                )

            if not final_state and event_captured_state:
                final_state = self._serialize_state_for_eval(event_captured_state)
                logger.info(
                    "state_captured_from_stream_event",
                    research_id=research_id,
                    verified_facts=len(final_state.get("verified_facts", [])),
                )

            if not final_state:
                logger.error(
                    "all_state_capture_paths_failed",
                    research_id=research_id,
                    checkpointer_set=self._checkpointer is not None,
                )

        except Exception as exc:
            # State capture failure must never block the completed status update.
            logger.warning(
                "final_state_capture_failed",
                research_id=research_id,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        # Update in-memory record
        self._jobs[research_id].update({
            "status": "completed",
            "state": final_state,
            "final_report": final_state.get("final_report"),
            "facts_count": len(final_state.get("verified_facts", [])),
            "entities_count": len(final_state.get("entities", [])),
            "risk_flags_count": len(final_state.get("risk_flags", [])),
            "overall_risk_score": final_state.get("overall_risk_score"),
            "audit_log": final_state.get("audit_log", []),
        })

        # Persist to Redis
        await self._redis_set_research_state(research_id, final_state)
        await self._redis_set_job(research_id, {
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
