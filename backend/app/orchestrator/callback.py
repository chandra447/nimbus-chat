"""Callback manager for A2A push-notification async pattern.

When the orchestrator delegates to a specialist using ``return_immediately=True``,
the specialist processes the task in the background and POSTs task events
(status updates, artifact chunks) to a webhook URL. This module provides:

- ``CallbackManager`` — in-memory registry mapping callback tokens to
  ``asyncio.Queue`` instances so the webhook endpoint can push events
  and the executor can consume them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CallbackManager:
    """Manages callback queues for async specialist responses."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def create_queue(self, token: str) -> asyncio.Queue:
        """Register a new callback queue keyed by ``token``."""
        q: asyncio.Queue = asyncio.Queue()
        self._queues[token] = q
        return q

    def get_queue(self, token: str) -> asyncio.Queue | None:
        return self._queues.get(token)

    async def push_event(self, token: str, event: Any) -> bool:
        """Push a push-notification event into the queue for ``token``.

        Returns True if the event was queued, False if the token is unknown
        (e.g. already cleaned up or never registered).
        """
        q = self._queues.get(token)
        if q is None:
            logger.debug('Callback token %s not found (expired?)', token)
            return False
        await q.put(event)
        return True

    def remove_queue(self, token: str) -> None:
        self._queues.pop(token, None)


# Module-level singleton — the FastAPI webhook handler and the executor
# both need to access the same instance.
callback_manager = CallbackManager()
