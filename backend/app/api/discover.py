"""POST /v1/discover: a themed digest of recent research on a topic, as a stream of events."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.ask import STREAM_RESPONSES, stream_pipeline
from app.api.schemas import DiscoverRequest
from app.auth.deps import current_user
from app.auth.users import User
from app.generation.plan import DateWindow

router = APIRouter(prefix="/v1", tags=["discover"])


def months_back(today: date, months: int) -> date:
    """The same day ``months`` months earlier, clamped to the end of shorter months."""
    month_index = today.year * 12 + today.month - 1 - months
    year, month = divmod(month_index, 12)
    for day in range(today.day, 0, -1):
        try:
            return date(year, month + 1, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def window_for(body: DiscoverRequest, today: date) -> DateWindow:
    start = body.start or months_back(body.end or today, body.months)
    return DateWindow(start=start, end=body.end)


@router.post("/discover", response_class=StreamingResponse, responses=STREAM_RESPONSES)
async def discover(
    body: DiscoverRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> StreamingResponse:
    today = datetime.now(UTC).date()
    return await stream_pipeline(
        request,
        user,
        name="discover",
        question=body.topic,
        history=[],
        window=window_for(body, today),
    )
