from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.ask import seconds_until_utc_midnight
from app.auth.tokens import Claims, mint_dev_token
from app.auth.users import User
from app.config import Settings
from app.db.queries.articles import ArticleDetail, Passage
from app.ingest.metadata import StudyMetadata
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


class FakeUsage:
    def __init__(self, spent: Decimal = Decimal(0)) -> None:
        self.spent = spent
        self.added: list[tuple[uuid.UUID, date, Decimal]] = []

    async def cost_on(self, user_id: uuid.UUID, day: date) -> Decimal:
        return self.spent

    async def add(self, user_id: uuid.UUID, day: date, cost_usd: Decimal) -> None:
        self.added.append((user_id, day, cost_usd))


class FakeQueryLogs:
    def __init__(self) -> None:
        self.feedback: list[tuple[int, uuid.UUID, int]] = []

    async def set_feedback(self, query_id: int, user_id: uuid.UUID, value: int) -> bool:
        self.feedback.append((query_id, user_id, value))
        return query_id == 7


class FakeArticles:
    async def get(self, pmid: int) -> ArticleDetail | None:
        if pmid != 90_000_001:
            return None
        return ArticleDetail(
            pmid=pmid,
            title="Synthetic trial",
            journal="J Test",
            pub_date=date(2024, 1, 1),
            doi=None,
            pub_types=("Randomized Controlled Trial",),
            publication_status="ppublish",
            is_retracted=False,
            study_meta=StudyMetadata(design="rct", sample_size=40),
            passages=(
                Passage(
                    chunk_id=11,
                    section="Abstract-RESULTS",
                    chunk_index=1,
                    content="Grip strength increased.",
                ),
            ),
        )


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


@contextmanager
def running(settings: Settings, usage: FakeUsage | None = None) -> Iterator[TestClient]:
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
        state = test_client.app.state  # type: ignore[attr-defined]
        state.users = FakeUsers()
        state.usage = usage or FakeUsage()
        state.query_logs = FakeQueryLogs()
        state.articles = FakeArticles()
        yield test_client


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with running(settings) as test_client:
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
    assert "query_id" in events[-1][1]
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


def test_usage_is_recorded_when_the_stream_ends(settings: Settings, token: str) -> None:
    usage = FakeUsage()
    with running(settings, usage) as test_client:
        body = {"question": "Does creatine help?"}
        assert test_client.post("/v1/ask", json=body, headers=auth(token)).status_code == 200
    [(user_id, day, cost)] = usage.added  # written by the time shutdown has drained writes
    assert user_id == uuid.uuid5(uuid.NAMESPACE_URL, "tester")
    assert day == datetime.now(UTC).date()
    assert cost == Decimal(0)


def test_daily_cost_cap_returns_429(settings: Settings, token: str) -> None:
    with running(settings, FakeUsage(spent=settings.user_daily_cost_cap_usd)) as test_client:
        body = {"question": "Does creatine help?"}
        response = test_client.post("/v1/ask", json=body, headers=auth(token))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(response.headers["retry-after"]) <= 86_400


def test_seconds_until_utc_midnight() -> None:
    now = datetime(2026, 10, 14, 23, 59, 30, tzinfo=UTC)
    assert seconds_until_utc_midnight(now) == 30


def test_feedback_is_recorded_for_the_owner(client: TestClient, token: str) -> None:
    body = {"query_id": 7, "value": -1}
    response = client.post("/v1/feedback", json=body, headers=auth(token))
    assert response.status_code == 204
    logs = client.app.state.query_logs  # type: ignore[attr-defined]
    assert logs.feedback == [(7, uuid.uuid5(uuid.NAMESPACE_URL, "tester"), -1)]


@pytest.mark.parametrize(
    ("body", "status"),
    [({"query_id": 8, "value": 1}, 404), ({"query_id": 7, "value": 2}, 422)],
    ids=["not-yours", "bad-value"],
)
def test_feedback_errors(client: TestClient, token: str, body: dict[str, int], status: int) -> None:
    assert client.post("/v1/feedback", json=body, headers=auth(token)).status_code == status


def test_article_with_passages(client: TestClient, token: str) -> None:
    response = client.get("/v1/articles/90000001", headers=auth(token))
    assert response.status_code == 200
    article = response.json()
    assert (article["design"], article["badge"]) == ("rct", "RCT")
    assert article["pubmed_url"] == "https://pubmed.ncbi.nlm.nih.gov/90000001/"
    assert article["passages"][0]["section"] == "Abstract-RESULTS"


def test_unknown_article_is_404(client: TestClient, token: str) -> None:
    response = client.get("/v1/articles/12345", headers=auth(token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_articles_need_a_token(client: TestClient) -> None:
    assert client.get("/v1/articles/90000001").status_code == 401
