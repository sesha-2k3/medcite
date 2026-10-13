"""Fixtures for tests that need the Compose Postgres.

Tests run against a throwaway database (``medcite_test`` by default), recreated once per
session, so the development database is never touched.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.session import create_engine
from tests.integration.helpers import recreate_database

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:dev@localhost:5432/medcite_test"
SAFE_TEST_DB_NAME = re.compile(r"^[a-z0-9_]+_test$")


@pytest.fixture(scope="session")
def database_url() -> str:
    raw = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    url = make_url(raw)
    if not SAFE_TEST_DB_NAME.match(url.database or ""):
        raise pytest.UsageError("TEST_DATABASE_URL must name a database ending in _test")
    try:
        asyncio.run(recreate_database(url))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable ({exc}); run: docker compose up -d db")
    return raw


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def migrated(alembic_config: Config) -> Config:
    command.upgrade(alembic_config, "head")
    return alembic_config


@pytest.fixture
async def engine(
    migrated: Config, database_url: str, make_settings: Callable[..., Settings]
) -> AsyncIterator[AsyncEngine]:
    db_engine = create_engine(make_settings(database_url=database_url))
    yield db_engine
    await db_engine.dispose()
