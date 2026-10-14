"""POST /v1/ask: answer a question as a stream of events."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.background import BackgroundWrites
from app.api.schemas import AskRequest
from app.api.sse import SSE_HEADERS, sse_stream
from app.auth.deps import current_user
from app.auth.users import User
from app.config import Settings
from app.db.queries.query_logs import UsageRepository
from app.errors import RateLimitedError, ServiceUnavailableError
from app.graph.build import Pipeline
from app.graph.state import RunControl
from app.models.llm import UsageMeter
from app.observability import request_id_var
from app.tracing import Tracer

router = APIRouter(prefix="/v1", tags=["ask"])


def seconds_until_utc_midnight(now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


@router.post(
    "/ask",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
)
async def ask(
    body: AskRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> StreamingResponse:
    state = request.app.state
    pipeline: Pipeline | None = state.pipeline
    if pipeline is None:
        raise ServiceUnavailableError("the answering pipeline is not loaded")
    settings: Settings = state.settings
    usage: UsageRepository = state.usage
    day: date = datetime.now(UTC).date()
    if await usage.cost_on(user.id, day) >= settings.user_daily_cost_cap_usd:
        raise RateLimitedError(
            "daily usage limit reached; it resets at midnight UTC",
            retry_after_s=seconds_until_utc_midnight(),
        )

    tracer: Tracer = state.tracer
    background: BackgroundWrites = state.background
    request_id = request_id_var.get() or uuid.uuid4().hex
    trace = tracer.start(
        seed=request_id,
        name="ask",
        user_id=str(user.id),
        input={"question": body.question, "history": body.history},
        metadata={"request_id": request_id},  # links the trace to the request's log lines
    )
    meter, control = UsageMeter(), RunControl()

    def close() -> None:
        """Runs however the stream ends; usage counts even when the client disconnects."""
        control.cancel()
        trace.end()
        background.spawn(usage.add(user.id, day, meter.cost_usd))

    events = pipeline.run(
        question=body.question,
        history=body.history,
        user_id=user.id,
        trace_id=trace.trace_id,
        meter=meter,
        control=control,
        trace=trace,
    )
    stream = sse_stream(
        events, heartbeat_s=settings.sse_heartbeat_s, trace_id=trace.trace_id, on_close=close
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
