from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import no_services

# Ports nothing listens on: connections are refused immediately.
UNREACHABLE_DB = "postgresql+asyncpg://postgres:dev@127.0.0.1:1/medcite"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build Settings without reading .env, so tests are hermetic."""

    def _make(**overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "app_env": "development",
            "auth_mode": "dev",
            "database_url": UNREACHABLE_DB,
            "redis_url": UNREACHABLE_REDIS,
            "db_connect_timeout_s": 1.0,
            "redis_connect_timeout_s": 1.0,
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    return _make


@pytest.fixture
def client(make_settings: Callable[..., Settings]) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(), services=no_services)) as c:
        yield c
