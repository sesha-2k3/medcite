"""POST /v1/feedback: rate an answer up or down."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.schemas import FeedbackRequest
from app.auth.deps import current_user
from app.auth.users import User
from app.db.queries.query_logs import QueryLogRepository
from app.errors import NotFoundError

router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def post_feedback(
    body: FeedbackRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> Response:
    logs: QueryLogRepository = request.app.state.query_logs
    if not await logs.set_feedback(body.query_id, user.id, body.value):
        raise NotFoundError("no answer with that ID belongs to you")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
