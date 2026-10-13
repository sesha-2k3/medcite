"""Database engine and session factory.

Supports direct and pooled connection strings (portability rule 12). With
``DB_STATEMENT_CACHE_SIZE=0`` statement caching is disabled and prepared statements get unique
names, as transaction-mode poolers require.

pgvector values travel as text: the ``Vector`` column type serializes arrays on the way in and
parses them back into numpy on the way out, so no asyncpg codec is registered.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def build_connect_args(settings: Settings) -> dict[str, Any]:
    """asyncpg connection arguments derived from settings."""
    cache = settings.db_statement_cache_size
    args: dict[str, Any] = {
        "timeout": settings.db_connect_timeout_s,
        "statement_cache_size": cache,
        "prepared_statement_cache_size": cache,
        "server_settings": {"application_name": settings.app_name.lower()},
    }
    if cache == 0:
        args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid4().hex}__"
    return args


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the application's async engine. Call once per process."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args=build_connect_args(settings),
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_database(engine: AsyncEngine) -> None:
    """Raise if the database is unreachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
