"""POST /v1/ask: answer a question as a stream of events."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import AskRequest
from app.api.sse import SSE_HEADERS, sse_stream
from app.auth.deps import current_user
from app.auth.users import User
from app.config import Settings
from app.errors import ServiceUnavailableError
from app.graph.build import Pipeline
from app.graph.state import RunControl
from app.observability import request_id_var

router = APIRouter(prefix="/v1", tags=["ask"])


@router.post(
    "/ask",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
)
async def ask(
    body: AskRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> StreamingResponse:
    pipeline: Pipeline | None = request.app.state.pipeline
    if pipeline is None:
        raise ServiceUnavailableError("the answering pipeline is not loaded")
    settings: Settings = request.app.state.settings
    trace_id = request_id_var.get() or uuid.uuid4().hex
    control = RunControl()
    events = pipeline.run(
        question=body.question,
        history=body.history,
        user_id=user.id,
        trace_id=trace_id,
        control=control,
    )
    stream = sse_stream(
        events, heartbeat_s=settings.sse_heartbeat_s, trace_id=trace_id, on_close=control.cancel
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
