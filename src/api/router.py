"""Top-level API router aggregating all v1 sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.v1.evaluations import router as evaluations_router
from src.api.v1.graph import router as graph_router
from src.api.v1.health import router as health_router
from src.api.v1.research import router as research_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(research_router)
api_router.include_router(graph_router)
api_router.include_router(evaluations_router)
