"""End-to-end test for the full research pipeline.

This test requires:
- Running Neo4j instance
- Running Redis instance
- Valid API keys (OpenRouter, Tavily)

Run with: make up && pytest tests/e2e/ -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    True,  # Skip by default; set to False for E2E testing
    reason="Requires full infrastructure and API keys",
)


@pytest.mark.asyncio
async def test_full_research_pipeline():
    """End-to-end: start research, wait for completion, verify results."""
    from fastapi.testclient import TestClient

    from src.main import app

    with TestClient(app) as client:
        # Start research
        resp = client.post(
            "/api/v1/research",
            json={
                "target_name": "Timothy Overturf",
                "target_context": "CEO of Sisu Capital",
                "objectives": ["biographical", "financial"],
                "max_depth": 2,
            },
        )
        assert resp.status_code == 200
        research_id = resp.json()["research_id"]

        # Poll for completion (with timeout)
        import asyncio
        import time

        start = time.time()
        timeout = 300  # 5 minutes

        while time.time() - start < timeout:
            status_resp = client.get(f"/api/v1/research/{research_id}/status")
            status = status_resp.json()["status"]
            if status in ("completed", "failed"):
                break
            await asyncio.sleep(5)

        assert status == "completed"

        # Get results
        result_resp = client.get(f"/api/v1/research/{research_id}")
        result = result_resp.json()
        assert result["final_report"] is not None
        assert result["facts_count"] > 0
