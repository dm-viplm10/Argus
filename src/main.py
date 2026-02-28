"""FastAPI application factory with lifespan events."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.dependencies import set_checkpointer, set_neo4j_conn, set_registry
from src.api.router import api_router
from src.config import get_settings
from src.graph_db.connection import Neo4jConnection
from src.graph_db.schema import init_schema
from src.models.llm_registry import LLMRegistry
from src.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown resources."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

    # Neo4j
    neo4j_conn = Neo4jConnection(settings)
    await neo4j_conn.connect()
    await init_schema(neo4j_conn)
    set_neo4j_conn(neo4j_conn)

    # LLM registry
    registry = LLMRegistry(settings)
    set_registry(registry)

    # Redis checkpointer
    try:
        from langgraph_checkpoint_redis import AsyncRedisSaver

        checkpointer = AsyncRedisSaver(redis_url=settings.REDIS_URL)
        set_checkpointer(checkpointer)
        logger.info("redis_checkpointer_initialized")
    except Exception as exc:
        logger.warning("redis_checkpointer_unavailable", error=str(exc))
        set_checkpointer(None)

    logger.info("app_started")
    yield

    # Shutdown
    await neo4j_conn.close()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

    application = FastAPI(
        title="Argus",
        description="Autonomous AI OSINT investigation agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router)

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "type": type(exc).__name__},
        )

    return application


app = create_app()
