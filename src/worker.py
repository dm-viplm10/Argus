"""Celery app and research task definition."""

from __future__ import annotations

import asyncio
import json

import redis as sync_redis
from celery import Celery

from src.config import get_settings
from src.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

settings = get_settings()

celery_app = Celery(
    "argus",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


_JOB_KEY = "argus:job:{}"
_JOB_TTL = 86400 * 7  # 7 days


def _write_job_status(research_id: str, data: dict) -> None:
    """Write job status to Redis from the Celery worker (sync)."""
    try:
        r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.set(_JOB_KEY.format(research_id), json.dumps(data), ex=_JOB_TTL)
        r.close()
    except Exception as exc:
        logger.warning("redis_status_write_failed", research_id=research_id, error=str(exc))


@celery_app.task(bind=True, name="research.run", max_retries=1)
def run_research_task(self, research_id: str, request: dict) -> dict:
    """Celery task that runs the full LangGraph research pipeline.

    Runs the async graph inside a fresh event loop. Uses Redis
    checkpointer so progress survives worker restarts.
    """
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    logger.info("celery_task_started", research_id=research_id, task_id=self.request.id)
    _write_job_status(research_id, {"status": "running", "task_id": self.request.id})

    try:
        result = asyncio.get_event_loop().run_until_complete(
            _execute_research(research_id, request)
        )
        _write_job_status(research_id, {
            "status": "completed",
            "task_id": self.request.id,
            "facts_count": len(result.get("verified_facts", [])),
            "entities_count": len(result.get("entities", [])),
            "risk_flags_count": len(result.get("risk_flags", [])),
            "overall_risk_score": result.get("overall_risk_score"),
            "final_report": result.get("final_report"),
            "audit_log": result.get("audit_log", []),
        })
        logger.info("celery_task_completed", research_id=research_id)
        return {"research_id": research_id, "status": "completed"}
    except Exception as exc:
        _write_job_status(research_id, {
            "status": "failed",
            "task_id": self.request.id,
            "error": str(exc),
        })
        logger.error("celery_task_failed", research_id=research_id, error=str(exc))
        raise self.retry(exc=exc, countdown=30)


async def _execute_research(research_id: str, request: dict) -> dict:
    """Async execution of the research graph within a Celery task."""
    from src.agent.graph import compile_research_graph
    from src.graph_db.connection import Neo4jConnection
    from src.models.llm_registry import LLMRegistry

    neo4j_conn = Neo4jConnection(settings)
    await neo4j_conn.connect()

    registry = LLMRegistry(settings)

    checkpointer = None
    try:
        from langgraph_checkpoint_redis import AsyncRedisSaver

        checkpointer = AsyncRedisSaver(redis_url=settings.REDIS_URL)
    except Exception:
        pass

    try:
        graph = compile_research_graph(settings, registry, neo4j_conn, checkpointer=checkpointer)

        initial_state = {
            "research_id": research_id,
            "target_name": request["target_name"],
            "target_context": request.get("target_context", ""),
            "research_objectives": request.get("objectives", []),
            "current_phase": 0,
            "max_phases": request.get("max_depth", 5),
            "iteration_count": 0,
            "phase_complete": False,
            "supervisor_instructions": "",
            # Per-phase progress flags — reset on every phase advance
            "current_phase_searched": False,
            "current_phase_analyzed": False,
            "current_phase_verified": False,
            "current_phase_risk_assessed": False,
            # Delta cursors — only advance, never reset
            "search_results_analyzed_count": 0,
            "scraped_content_analyzed_count": 0,
            "facts_verified_count": 0,
            "risk_assessed_facts_count": 0,
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

        # recursion_limit covers all supervisor hops across the full pipeline.
        # A 5-phase run needs ~50-60 hops minimum; 150 gives headroom for retries.
        config = {"configurable": {"thread_id": research_id}, "recursion_limit": 150}
        result = await graph.ainvoke(initial_state, config)
        return result

    finally:
        await neo4j_conn.close()
