"""Verify all infrastructure components and API keys are working."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx


async def check_neo4j() -> bool:
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "research_agent_dev")
    try:
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS test")
            record = await result.single()
            assert record and record["test"] == 1
        await driver.close()
        print("[OK] Neo4j connection successful")
        return True
    except Exception as exc:
        print(f"[FAIL] Neo4j: {exc}")
        return False


async def check_redis() -> bool:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        from redis.asyncio import from_url

        client = from_url(url)
        pong = await client.ping()
        assert pong is True
        await client.aclose()
        print("[OK] Redis connection successful")
        return True
    except Exception as exc:
        print(f"[FAIL] Redis: {exc}")
        return False


async def check_openrouter() -> bool:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("[FAIL] OpenRouter: OPENROUTER_API_KEY not set")
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            models = {m["id"] for m in data.get("data", [])}
            needed = [
                "anthropic/claude-sonnet-4.6",
                "google/gemini-2.5-pro",
                "x-ai/grok-3",
                "openai/gpt-4.1-mini",
            ]
            for slug in needed:
                found = any(slug in m for m in models)
                status = "OK" if found else "WARN"
                print(f"  [{status}] Model {slug}: {'available' if found else 'not found'}")
        print("[OK] OpenRouter API accessible")
        return True
    except Exception as exc:
        print(f"[FAIL] OpenRouter: {exc}")
        return False


async def check_tavily() -> bool:
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        print("[FAIL] Tavily: TAVILY_API_KEY not set")
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": "test", "max_results": 1},
                timeout=15,
            )
            resp.raise_for_status()
        print("[OK] Tavily search API working")
        return True
    except Exception as exc:
        print(f"[FAIL] Tavily: {exc}")
        return False


async def check_langsmith() -> bool:
    api_key = os.getenv("LANGSMITH_API_KEY", "")
    if not api_key:
        print("[SKIP] LangSmith: LANGSMITH_API_KEY not set (optional)")
        return True
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.smith.langchain.com/info",
                headers={"x-api-key": api_key},
                timeout=10,
            )
            resp.raise_for_status()
        print("[OK] LangSmith API accessible")
        return True
    except Exception as exc:
        print(f"[FAIL] LangSmith: {exc}")
        return False


async def main() -> None:
    print("=" * 50)
    print("Argus — Infrastructure Verification")
    print("=" * 50)

    results = await asyncio.gather(
        check_neo4j(),
        check_redis(),
        check_openrouter(),
        check_tavily(),
        check_langsmith(),
    )

    print("=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} checks passed")
    if not all(results):
        print("Some checks failed. Review output above.")
        sys.exit(1)
    print("All systems operational.")


if __name__ == "__main__":
    asyncio.run(main())
