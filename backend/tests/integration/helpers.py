"""Database utilities for integration tests, independent of the app's own engine."""

from __future__ import annotations

import asyncpg
from sqlalchemy.engine import URL, make_url


def asyncpg_dsn(url: URL, database: str | None = None) -> str:
    target = url.set(drivername="postgresql", database=database or url.database)
    return target.render_as_string(hide_password=False)


async def recreate_database(url: URL) -> None:
    admin = await asyncpg.connect(asyncpg_dsn(url, "postgres"), timeout=3)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await admin.close()


async def fetch_public_tables(database_url: str) -> set[str]:
    conn = await asyncpg.connect(asyncpg_dsn(make_url(database_url)))
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    finally:
        await conn.close()
    return {row["tablename"] for row in rows}
