"""FastAPI application factory.

Run with: ``uv run uvicorn app.main:app --reload --port 8000``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from app.config import Settings, get_settings
from app.db.redis import create_redis, ping_redis
from app.db.session import create_engine, create_session_factory, ping_database
from app.errors import install_error_handlers
from app.observability import configure_logging, configure_sentry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own ``Settings``; production reads the environment."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    configure_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = create_redis(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.readiness_checks = {
            "database": lambda: ping_database(engine),
            "redis": lambda: ping_redis(redis),
        }
        # MedCPT model singletons are loaded here from M3 onward.
        logger.info("startup", extra={"app_env": settings.app_env})
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
    )
    # Middleware runs outermost-last: CORS wraps the request-ID middleware.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    install_error_handlers(app)
    app.include_router(health.router)
    # /v1 routers (ask, discover, articles, feedback) are added in M7.
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Lazy ``app`` attribute for ``uvicorn app.main:app``.

    Importing ``create_app`` (as tests do) therefore never reads the environment.
    """
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
