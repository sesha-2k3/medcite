"""Server-Sent Events over POST: event formatting, heartbeat, and cancellation.

The pipeline runs in its own task feeding a queue. The response generator sends each event,
a ``: ping`` comment after ``heartbeat_s`` without one (proxies close idle streams), and an
``error`` event if the pipeline fails. When the client disconnects, Starlette closes the
generator, whose ``finally`` calls ``on_close`` (which cancels the running graph node) and
cancels the pipeline task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable

from app.errors import AppError
from app.graph.events import Event

logger = logging.getLogger(__name__)

PING = ": ping\n\n"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def format_event(event: Event) -> str:
    data = json.dumps(event.data, default=str, ensure_ascii=False)
    return f"event: {event.name}\ndata: {data}\n\n"


def error_event(code: str, message: str, trace_id: str) -> Event:
    return Event("error", {"code": code, "message": message, "trace_id": trace_id})


async def sse_stream(
    events: AsyncIterator[Event],
    *,
    heartbeat_s: float,
    trace_id: str,
    on_close: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """``on_close`` runs when the stream ends for any reason, including a disconnect, before
    the pipeline task is cancelled.
    """
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except AppError as exc:
            logger.warning("pipeline_error", extra={"code": exc.code, "reason": exc.message})
            await queue.put(error_event(exc.code, exc.message, trace_id))
        except Exception:
            logger.exception("pipeline_failed")
            await queue.put(error_event("internal_error", "Internal server error", trace_id))
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield PING
                continue
            if item is None:
                return
            yield format_event(item)
    finally:
        if on_close is not None:
            on_close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
