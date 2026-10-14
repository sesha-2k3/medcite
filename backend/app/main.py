"""FastAPI application factory.

Run with: ``uv run uvicorn app.main:app --reload --port 8000``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import articles, ask, feedback, health
from app.api.background import BackgroundWrites
from app.api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from app.auth.tokens import build_verifier
from app.auth.users import UserDirectory
from app.config import Settings, get_settings
from app.db.queries.articles import ArticleRepository
from app.db.queries.query_logs import QueryLogRepository, UsageRepository
from app.db.redis import create_redis, ping_redis
from app.db.session import create_engine, create_session_factory, ping_database
from app.errors import install_error_handlers
from app.graph.build import Pipeline
from app.observability import configure_logging, configure_sentry
from app.services import Resources, ServicesFactory, build_services
from app.sources.http import create_http_client
from app.tracing import build_tracer

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, services: ServicesFactory | None = None
) -> FastAPI:
    """Build the app. Tests pass their own ``Settings`` and a ``services`` factory with fakes
    (or ``no_services``); production reads the environment and loads the real services.
    """
    settings = settings or get_settings()
    make_services = services or build_services
    configure_logging(settings.log_level)
    configure_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = create_redis(settings)
        http = create_http_client(settings)
        sessions = create_session_factory(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = sessions
        app.state.redis = redis
        app.state.verifier = build_verifier(settings, http)
        app.state.users = UserDirectory(sessions)
        app.state.usage = UsageRepository(sessions)
        app.state.query_logs = QueryLogRepository(sessions)
        app.state.articles = ArticleRepository(sessions)
        app.state.tracer = build_tracer(settings)
        app.state.background = BackgroundWrites()
        app.state.readiness_checks = {
            "database": lambda: ping_database(engine),
            "redis": lambda: ping_redis(redis),
        }
        built = await make_services(settings, Resources(http=http, redis=redis, sessions=sessions))
        app.state.pipeline = Pipeline(built) if built is not None else None
        logger.info("startup", extra={"app_env": settings.app_env})
        try:
            yield
        finally:
            await app.state.background.drain()
            app.state.tracer.shutdown()
            await http.aclose()
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
    app.include_router(ask.router)
    app.include_router(articles.router)
    app.include_router(feedback.router)
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
