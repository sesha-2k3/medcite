from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import Claims, mint_dev_token
from app.auth.users import User
from app.config import Settings
from app.main import create_app
from app.services import PipelineConfig, no_services
from tests.generation.builders import creatine_passages, stored
from tests.graph.fakes import FakeRetriever, FakeSearcher, factory, fake_services

DEV_KEY = "dev-signing-key-for-tests-only-0123456789abcdef"
ALL = [90_000_001, 90_000_002, 90_000_003]


class FakeUsers:
    def __init__(self) -> None:
        self.seen: list[Claims] = []

    async def resolve(self, claims: Claims) -> User:
        self.seen.append(claims)
        return User(id=uuid.uuid5(uuid.NAMESPACE_URL, claims.subject), email=claims.email)


def parse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.split("\n") if not line.startswith(":")]
        fields = dict(line.split(": ", 1) for line in lines)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(auth_dev_signing_key=DEV_KEY)


@pytest.fixture
def token(settings: Settings) -> str:
    return mint_dev_token(DEV_KEY, subject="tester", audience=settings.auth_audience)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    services, _ = fake_services(
        settings,
        [stored("generate_conflict"), stored("judge_supported")],
        searcher=FakeSearcher({"q1": ALL}),
        retriever=FakeRetriever(
            lambda tiers: [
                replace(p, tier=tiers.get(p.scored.chunk.pmid)) for p in creatine_passages()
            ]
        ),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    with TestClient(create_app(settings, services=factory(services))) as test_client:
        test_client.app.state.users = FakeUsers()  # type: ignore[attr-defined]
        yield test_client


def test_ask_streams_the_answer(client: TestClient, token: str) -> None:
    body = {"question": "Does creatine help?"}
    response = client.post("/v1/ask", json=body, headers=auth(token))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse(response.text)
    order = [name for name, _ in events if name != "step"]
    assert order[0] == "token"
    assert order[-4:] == ["citations", "evidence_table", "rating", "done"]
    assert events[-1][1]["trace_id"] == response.headers["x-request-id"]
    assert client.app.state.users.seen[0].subject == "tester"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer not-a-jwt"}],
    ids=["missing", "wrong-scheme", "malformed"],
)
def test_unauthenticated_requests_get_401(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v1/ask", json={"question": "Does creatine help?"}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_expired_token_gets_401(client: TestClient, settings: Settings) -> None:
    expired = mint_dev_token(
        DEV_KEY, subject="tester", audience=settings.auth_audience, ttl_s=1, now=1_000_000
    )
    body = {"question": "Does creatine help?"}
    response = client.post("/v1/ask", json=body, headers=auth(expired))
    assert response.status_code == 401


def test_invalid_body_is_rejected(client: TestClient, token: str) -> None:
    response = client.post("/v1/ask", json={"question": "x"}, headers=auth(token))
    assert response.status_code == 422


def test_without_a_pipeline_ask_is_unavailable(settings: Settings, token: str) -> None:
    with TestClient(create_app(settings, services=no_services)) as test_client:
        test_client.app.state.users = FakeUsers()  # type: ignore[attr-defined]
        response = test_client.post(
            "/v1/ask", json={"question": "Does creatine help?"}, headers=auth(token)
        )
    assert response.status_code == 503


def test_unhandled_errors_carry_a_trace_id(make_settings: Callable[..., Settings]) -> None:
    app = create_app(make_settings(), services=no_services)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom", headers={"x-request-id": "trace-abcdef12"})
    assert response.status_code == 500
    assert response.json()["error"]["trace_id"] == "trace-abcdef12"
