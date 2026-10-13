"""Alembic environment: runs migrations over asyncpg against the direct database URL."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import models  # noqa: F401  registers tables on Base.metadata
from app.db.base import Base
from app.db.session import build_connect_args
from app.observability import configure_logging

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    """An explicit URL (set by tests via ``config.attributes``) wins over settings."""
    explicit: str | None = config.attributes.get("database_url")
    return explicit or get_settings().migrations_database_url


def run_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(
        database_url(),
        poolclass=pool.NullPool,
        connect_args=build_connect_args(get_settings()),
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


configure_logging(get_settings().log_level)
if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
