"""Typed errors and their mapping to the API's single error shape.

{"error": {"code": "...", "message": "...", "trace_id": "..."}}
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors the API knows how to report."""

    code = "internal_error"
    status_code = 500

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_s = retry_after_s


class UpstreamError(AppError):
    """An external service (NCBI, LLM provider) failed or returned garbage."""

    code = "upstream_error"
    status_code = 502


class RateLimitedError(AppError):
    """An external service, or our own per-user limit, refused the request for now."""

    code = "rate_limited"
    status_code = 429


class LLMOutputError(AppError):
    """The LLM's output could not be parsed or validated, even after a repair attempt."""

    code = "llm_output_invalid"
    status_code = 502


class ReadOnlyError(AppError):
    code = "read_only"
    status_code = 503


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message, "trace_id": request_id_var.get() or ""}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if exc.retry_after_s is not None:
            headers["Retry-After"] = str(max(1, round(exc.retry_after_s)))
        return JSONResponse(
            error_body(exc.code, exc.message), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            error_body(f"http_{exc.status_code}", str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(error_body("invalid_request", str(exc.errors())), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)
