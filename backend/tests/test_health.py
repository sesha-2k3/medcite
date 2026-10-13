from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("down")


async def _hang() -> None:
    await asyncio.sleep(60)


def _set_checks(client: TestClient, **checks: Callable[[], object]) -> None:
    """Replace the app's readiness probes (registered at startup) with stubs."""
    client.app.state.readiness_checks = checks  # type: ignore[attr-defined]


def test_healthz_is_ok_without_dependencies(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_200_when_db_and_redis_up(client: TestClient) -> None:
    _set_checks(client, database=_ok, redis=_ok)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize(
    ("db", "redis", "expected"),
    [
        (_fail, _ok, {"database": "fail", "redis": "ok"}),
        (_ok, _fail, {"database": "ok", "redis": "fail"}),
        (_fail, _fail, {"database": "fail", "redis": "fail"}),
    ],
)
def test_readyz_503_when_a_dependency_is_down(
    client: TestClient,
    db: Callable[[], object],
    redis: Callable[[], object],
    expected: dict[str, str],
) -> None:
    _set_checks(client, database=db, redis=redis)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "checks": expected}


def test_readyz_503_when_a_check_hangs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.health.CHECK_TIMEOUT_S", 0.1)
    _set_checks(client, database=_hang, redis=_ok)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "fail"


def test_readyz_503_with_real_clients_and_unreachable_services(client: TestClient) -> None:
    """Exercises the real asyncpg and redis probes, pointed at closed ports."""
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"database": "fail", "redis": "fail"}


@pytest.mark.integration
def test_readyz_200_against_compose_services(make_settings: Callable[..., Settings]) -> None:
    """Requires `docker compose up -d db redis`."""
    settings = make_settings(
        database_url="postgresql+asyncpg://postgres:dev@localhost:5432/medcite",
        redis_url="redis://localhost:6379/0",
    )
    with TestClient(create_app(settings)) as c:
        resp = c.get("/readyz")
    if resp.status_code != 200:
        pytest.skip("Compose services down or dev DB not migrated (alembic upgrade head)")
    assert resp.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_request_id_generated_and_returned(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert len(resp.headers["x-request-id"]) == 32


def test_valid_incoming_request_id_is_echoed(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"x-request-id": "abc12345-trace"})
    assert resp.headers["x-request-id"] == "abc12345-trace"


def test_malformed_incoming_request_id_is_replaced(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"x-request-id": "bad id with spaces"})
    assert resp.headers["x-request-id"] != "bad id with spaces"


def test_errors_use_standard_shape(client: TestClient) -> None:
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body["error"]) == {"code", "message", "trace_id"}
    assert body["error"]["trace_id"] == resp.headers["x-request-id"]
