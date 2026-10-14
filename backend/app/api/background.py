"""Writes that must outlive the request, such as usage after a client disconnects.

A disconnect cancels the response's own task, so its cleanup cannot await a database write;
the write runs as a separate task instead, and app shutdown waits for any still running.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundWrites:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, write: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(self._run(write))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    @staticmethod
    async def _run(write: Coroutine[Any, Any, None]) -> None:
        try:
            await write
        except Exception:
            logger.exception("background_write_failed")
