"""API client for Argus backend."""

from __future__ import annotations

import json
from typing import Any

import requests


def get_base_url() -> str:
    """Backend API base URL (no trailing slash)."""
    import os
    return (os.environ.get("ARGUS_API_URL") or "http://localhost:8000").rstrip("/")


def start_research(
    target_name: str,
    target_context: str = "",
    objectives: list[str] | None = None,
    max_depth: int | None = None,
) -> dict[str, Any]:
    """POST /api/v1/research — start a new research run. Returns {research_id, status, created_at}."""
    if objectives is None:
        objectives = ["biographical", "financial", "risk_assessment", "connections"]
    url = f"{get_base_url()}/api/v1/research"
    payload = {
        "target_name": target_name,
        "target_context": target_context,
        "objectives": objectives,
    }
    if max_depth is not None:
        payload["max_depth"] = max_depth
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def get_research(research_id: str) -> dict[str, Any]:
    """GET /api/v1/research/{id} — full result including final_report."""
    url = f"{get_base_url()}/api/v1/research/{research_id}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def stream_research(research_id: str, timeout: int = 3600):
    """GET /api/v1/research/{id}/stream — SSE stream. Returns response with stream=True."""
    url = f"{get_base_url()}/api/v1/research/{research_id}/stream"
    return requests.get(url, stream=True, timeout=timeout)


def health() -> dict[str, Any]:
    """GET /api/v1/health."""
    url = f"{get_base_url()}/api/v1/health"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    return r.json()


def ready() -> dict[str, Any]:
    """GET /api/v1/ready."""
    url = f"{get_base_url()}/api/v1/ready"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    return r.json()


def run_evaluation(
    research_id: str,
    ground_truth_file: str = "timothy_overturf.json",
    use_llm_judge: bool = True,
) -> dict[str, Any]:
    """POST /api/v1/evaluate — run evaluation for a completed research job.
    Returns EvaluationResponse: evaluation_id, research_id, metrics, summary, evaluation_report.
    """
    url = f"{get_base_url()}/api/v1/evaluate"
    payload = {
        "research_id": research_id,
        "ground_truth_file": ground_truth_file,
        "use_llm_judge": use_llm_judge,
    }
    r = requests.post(url, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def get_graph(research_id: str) -> dict[str, Any]:
    """GET /api/v1/graph/{id} — graph as JSON (nodes, edges, counts)."""
    url = f"{get_base_url()}/api/v1/graph/{research_id}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def get_graph_image(research_id: str, format: str = "png") -> bytes:
    """GET /api/v1/graph/{id}/export?format=png|jpeg — graph as image bytes for display."""
    url = f"{get_base_url()}/api/v1/graph/{research_id}/export"
    r = requests.get(url, params={"format": format}, timeout=30)
    r.raise_for_status()
    return r.content
