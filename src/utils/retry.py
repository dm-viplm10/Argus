"""Async exponential backoff retry decorator."""

from __future__ import annotations

import asyncio
import functools
import random
from typing import Any, Callable, TypeVar

from src.utils.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    non_retryable_status_codes: tuple[int, ...] = (400, 401, 403, 404, 422),
) -> Callable[[F], F]:
    """Decorator for async functions with exponential backoff + jitter.

    Does not retry on client errors (4xx except 429) by default.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status and status in non_retryable_status_codes:
                        logger.warning(
                            "retry_skipped_client_error",
                            func=func.__name__,
                            status=status,
                        )
                        raise

                    if attempt == max_attempts:
                        raise

                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    jitter = random.uniform(0, delay * 0.5)
                    total_delay = delay + jitter
                    logger.warning(
                        "retry_attempt",
                        func=func.__name__,
                        attempt=attempt,
                        delay=round(total_delay, 2),
                        error=str(exc),
                    )
                    await asyncio.sleep(total_delay)

            raise RuntimeError(f"Exhausted retries for {func.__name__}") from last_exc

        return wrapper  # type: ignore[return-value]

    return decorator
