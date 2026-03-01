"""Deep web scraping tool using httpx + BeautifulSoup + trafilatura."""

from __future__ import annotations

import asyncio
import random
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from langchain_core.tools import BaseTool
from pydantic import Field

from src.utils.logging import get_logger

logger = get_logger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

_domain_last_request: dict[str, float] = {}
_POLITENESS_DELAY = 2.0


class WebScrapeTool(BaseTool):
    """Scrape and extract content from a URL.

    Uses httpx for async HTTP, BeautifulSoup for parsing, and trafilatura
    for article content extraction. Includes politeness delays, user-agent
    rotation, and retry logic.
    """

    name: str = "web_scrape"
    description: str = (
        "Scrape a web page and extract its main textual content. "
        "Input should be a URL string. Returns extracted text."
    )
    timeout: int = Field(default=30)
    max_retries: int = Field(default=3)

    async def _arun(self, url: str) -> str:
        return await self._scrape(url)

    def _run(self, url: str) -> str:
        return asyncio.get_event_loop().run_until_complete(self._scrape(url))

    async def _scrape(self, url: str) -> str:
        domain = urlparse(url).netloc
        now = asyncio.get_event_loop().time()
        last = _domain_last_request.get(domain, 0.0)
        if now - last < _POLITENESS_DELAY:
            await asyncio.sleep(_POLITENESS_DELAY - (now - last))
        _domain_last_request[domain] = asyncio.get_event_loop().time()

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                headers = {"User-Agent": random.choice(USER_AGENTS)}
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type:
                    return f"[PDF content at {url} — extraction not supported in this tool]"

                html = resp.text
                text = self._extract_text(html, url)
                if text:
                    logger.info("scrape_success", url=url, length=len(text))
                    return text[:15_000]  # Cap at 15k chars to keep ReAct context manageable
                return f"[No extractable content at {url}]"

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404):
                    return f"[HTTP {exc.response.status_code} for {url}]"
                last_error = exc
            except Exception as exc:
                last_error = exc

            backoff = (2**attempt) + random.uniform(0, 1)
            logger.warning("scrape_retry", url=url, attempt=attempt + 1, backoff=backoff)
            await asyncio.sleep(backoff)

        return f"[Scrape failed after {self.max_retries} attempts: {last_error}]"

    def _extract_text(self, html: str, url: str) -> str:
        try:
            text = trafilatura.extract(html, url=url, include_tables=True, include_links=True)
            if text and len(text) > 100:
                return text
        except Exception:
            pass

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
