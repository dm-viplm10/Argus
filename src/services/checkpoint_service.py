"""Redis checkpoint status reader for querying graph execution state."""

from __future__ import annotations

from typing import Any

from src.utils.logging import get_logger

logger = get_logger(__name__)


class CheckpointService:
    """Read LangGraph checkpoints from Redis for status reporting.

    When a research graph is running with the Redis checkpointer,
    each node's completion writes a checkpoint. This service reads
    the latest checkpoint to report progress without waiting for
    full completion.
    """

    def __init__(self, checkpointer: Any) -> None:
        self._checkpointer = checkpointer

    async def get_latest_state(self, thread_id: str) -> dict[str, Any] | None:
        """Get the latest checkpoint state for a research thread."""
        if self._checkpointer is None:
            return None

        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = await self._checkpointer.aget(config)
            if checkpoint and "channel_values" in checkpoint:
                return checkpoint["channel_values"]
            return None
        except Exception as exc:
            logger.warning("checkpoint_read_failed", thread_id=thread_id, error=str(exc))
            return None
