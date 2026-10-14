from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.api.sse import PING, format_event, sse_stream
from app.errors import UpstreamError
from app.graph.events import Event


async def events_of(*items: Event | Exception, delay: float = 0.0) -> AsyncIterator[Event]:
    for item in items:
        await asyncio.sleep(delay)
        if isinstance(item, Exception):
            raise item
        yield item


async def drain(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


def test_event_format() -> None:
    text = format_event(Event("token", {"text": "na\u00efve [C1]"}))
    assert text == 'event: token\ndata: {"text": "na\u00efve [C1]"}\n\n'


async def test_events_pass_through_in_order() -> None:
    stream = sse_stream(
        events_of(Event("step", {"node": "plan"}), Event("done", {})), heartbeat_s=5, trace_id="t"
    )
    chunks = await drain(stream)
    assert [c.split("\n")[0] for c in chunks] == ["event: step", "event: done"]


async def test_heartbeat_while_the_pipeline_is_quiet() -> None:
    stream = sse_stream(events_of(Event("done", {}), delay=0.05), heartbeat_s=0.01, trace_id="t")
    chunks = await drain(stream)
    assert chunks[0] == PING
    assert chunks[-1].startswith("event: done")


async def test_app_errors_become_an_error_event() -> None:
    stream = sse_stream(
        events_of(Event("step", {}), UpstreamError("PubMed is down")), heartbeat_s=5, trace_id="t9"
    )
    last = (await drain(stream))[-1]
    assert last.startswith("event: error\n")
    payload = json.loads(last.split("data: ", 1)[1])
    assert payload == {"code": "upstream_error", "message": "PubMed is down", "trace_id": "t9"}


async def test_unexpected_errors_are_not_leaked() -> None:
    stream = sse_stream(events_of(RuntimeError("secret detail")), heartbeat_s=5, trace_id="t")
    last = (await drain(stream))[-1]
    assert "internal_error" in last
    assert "secret detail" not in last


async def test_closing_the_stream_cancels_the_pipeline() -> None:
    state = {"cancelled": False}

    async def slow() -> AsyncIterator[Event]:
        yield Event("step", {"node": "plan"})
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        yield Event("done", {})

    stream = sse_stream(slow(), heartbeat_s=5, trace_id="t")
    first = await anext(stream)
    assert first.startswith("event: step")
    await asyncio.sleep(0.01)
    await stream.aclose()  # what Starlette does when the client disconnects
    assert state["cancelled"] is True


async def test_on_close_runs_when_the_client_disconnects() -> None:
    closed: list[bool] = []

    async def endless() -> AsyncIterator[Event]:
        while True:
            yield Event("step", {})
            await asyncio.sleep(1)

    stream = sse_stream(
        endless(), heartbeat_s=5, trace_id="t", on_close=lambda: closed.append(True)
    )
    await anext(stream)
    await stream.aclose()
    assert closed == [True]
