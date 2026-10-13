"""Health endpoints.

``/healthz`` answers whether the process is alive; it touches no dependencies.
``/readyz`` answers whether this instance can serve traffic: database and Redis must respond.
Readiness probes are registered on ``app.state.readiness_checks`` so tests can replace them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

ReadinessCheck = Callable[[], Awaitable[None]]
CHECK_TIMEOUT_S = 3.0


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "fail"]]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readyz(request: Request, response: Response) -> ReadinessResponse:
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    names = list(checks)
    results = await asyncio.gather(*(_run(name, checks[name]) for name in names))
    outcome = dict(zip(names, results, strict=True))
    ready = all(v == "ok" for v in outcome.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if ready else "unavailable", checks=outcome)


async def _run(name: str, check: ReadinessCheck) -> Literal["ok", "fail"]:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # any failure means "not ready"; details go to logs only
        logger.warning("readiness check failed", extra={"check": name, "error": repr(exc)})
        return "fail"
    return "ok"
