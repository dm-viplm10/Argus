"""Research API endpoints — start, status, results, and SSE streaming.

All job state and business logic lives in ResearchService. These handlers
are thin HTTP adapters: validate input, delegate to the service, return the
response. No module-level mutable state, no Redis calls, no graph logic.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.dependencies import get_research_service
from src.api.v1.schemas.research import (
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
    ResearchStatus,
)
from src.api.v1.sse_mapper import to_sse_event
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.services.research_service import ResearchService

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["research"])


@router.post("", response_model=ResearchResponse)
async def start_research(
    request: ResearchRequest,
    svc: ResearchService = Depends(get_research_service),
) -> ResearchResponse:
    """Start a new research investigation.

    Queues a background asyncio task that runs the LangGraph supervisor agent.
    """
    result = await svc.create_job(request)
    return ResearchResponse(
        research_id=result["research_id"],
        status=result["status"],
        created_at=result["created_at"],
    )


@router.get("/{research_id}", response_model=ResearchResult)
async def get_research(
    research_id: str,
    svc: ResearchService = Depends(get_research_service),
) -> ResearchResult:
    """Get full research results."""
    data = await svc.get_job_result(research_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Research not found")
    return ResearchResult(**data)


@router.get("/{research_id}/status", response_model=ResearchStatus)
async def get_research_status(
    research_id: str,
    svc: ResearchService = Depends(get_research_service),
) -> ResearchStatus:
    """Get real-time research status.

    Reads lifecycle status and live graph progress from the LangGraph
    checkpoint in Redis when the job is running.
    """
    data = await svc.get_status(research_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Research not found")
    return ResearchStatus(**data)


@router.delete("/{research_id}/cancel", status_code=200)
async def cancel_research(
    research_id: str,
    svc: ResearchService = Depends(get_research_service),
) -> dict:
    """Cancel a queued or running research job.

    Returns 409 if the job is already in a terminal state.
    """
    result = await svc.cancel_job(research_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if result.get("already_terminal"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job with status '{result['status']}'",
        )
    return {"research_id": research_id, "status": "cancelled"}


@router.get("/{research_id}/stream")
async def stream_research(
    research_id: str,
    svc: ResearchService = Depends(get_research_service),
) -> EventSourceResponse:
    """SSE endpoint — pipes graph node events to the client in real time.

    Raw LangGraph events are pulled from the job's event queue (written by
    ResearchService._run_job) and mapped to the client-facing SSE format by
    sse_mapper.to_sse_event. A final ``done`` event signals the stream is over.
    """

    async def event_generator():
        queue = svc.get_event_queue(research_id)

        if queue is None:
            # No live queue: job finished before the client connected, or not found.
            data = await svc.get_job_result(research_id)
            if data:
                yield {"event": "done", "data": json.dumps({"status": data.get("status", "unknown")})}
            else:
                yield {"event": "error", "data": json.dumps({"error": "not_found"})}
            return

        try:
            while True:
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=300)
                except TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue

                if raw is None:
                    # Sentinel pushed by _run_job to signal the graph finished.
                    data = await svc.get_job_result(research_id)
                    status = data.get("status", "completed") if data else "completed"
                    yield {"event": "done", "data": json.dumps({"status": status})}
                    return

                # Map raw LangGraph event to the client-facing SSE format.
                mapped = to_sse_event(raw)
                if mapped is not None:
                    event_type, event_data = mapped
                    yield {"event": event_type, "data": json.dumps(event_data)}

        finally:
            # Release the queue slot when the client disconnects or the stream ends.
            svc.release_event_queue(research_id)

    return EventSourceResponse(event_generator())
