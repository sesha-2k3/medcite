"""POST /v1/ask end to end on Postgres: real ingestion of the minimized biceps fixtures,
real hybrid retrieval and user upsert; scripted LLM, fake embedder and scorer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.tokens import DEV_ISSUER, DevTokenVerifier, mint_dev_token
from app.auth.users import UserDirectory
from app.config import Settings
from app.db.models import Article, User
from app.generation.generate import Generator
from app.generation.verify import Judge, Verifier
from app.graph.build import Pipeline
from app.ingest.service import IngestService
from app.ingest.store import ArticleStore
from app.main import create_app
from app.models.llm import LLMClient
from app.retrieval.factory import build_retriever
from app.services import PipelineConfig, Services
from tests.graph.fakes import FakePlanner, FakeSearcher, question_plan
from tests.ingest.fakes import FixtureFetcher, HashEmbedder, PubTypeMetadata
from tests.llm_fakes import ScriptedProvider

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/pubmed/minimized/efetch_biceps_curl.xml"
BICEPS_PMIDS = [39364857, 40082069, 29533715, 28617715, 40586278]
DEV_KEY = "dev-signing-key-for-tests-only-0123456789abcdef"
SUBJECT = "e2e-user"

ANSWER = {
    "direct_answer": (
        "No systematic review or meta-analysis addressing this question was found. "
        "Training studies of the elbow flexors were found instead [C1]."
    ),
    "synthesis": "The retrieved passages describe elbow-flexor training studies [C1].",
    "evidence_rows": [{"marker": "C1", "finding": "An elbow-flexor training study"}],
    "rating": {"level": "very_low", "justification": "Only indirect primary studies."},
}
JUDGE = {"claims": [], "gap_statement_ok": True, "conflict_omitted": False}


class RankScorer:
    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [10.0 - index for index in range(len(passages))]


@pytest.fixture
async def clean_sessions(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """The fixture PMIDs are real (below the synthetic range the shared fixture clears), so
    this test removes its articles and user itself, before and after, or other integration
    tests would find them in the index.
    """

    async def clear() -> None:
        async with sessions.begin() as session:
            await session.execute(delete(Article).where(Article.pmid.in_(BICEPS_PMIDS)))
            await session.execute(
                delete(User).where(User.auth_issuer == DEV_ISSUER, User.auth_subject == SUBJECT)
            )

    await clear()
    yield sessions
    await clear()


def parse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.split("\n") if not line.startswith(":")]
        fields = dict(line.split(": ", 1) for line in lines)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_ask_streams_a_verified_answer_end_to_end(
    clean_sessions: async_sessionmaker[AsyncSession], make_settings: Callable[..., Settings]
) -> None:
    sessions = clean_sessions
    settings = make_settings(auth_dev_signing_key=DEV_KEY)
    llm = LLMClient(settings, ScriptedProvider([json.dumps(ANSWER), json.dumps(JUDGE)]))
    embedder = HashEmbedder()
    services = Services(
        planner=FakePlanner(question_plan()),
        searcher=FakeSearcher({"q2": BICEPS_PMIDS}),
        ingester=IngestService(
            fetcher=FixtureFetcher(FIXTURE),
            extractor=PubTypeMetadata(),
            embedder=embedder,
            store=ArticleStore(sessions),
        ),
        retriever=build_retriever(settings, sessions, embedder, RankScorer()),
        generator=Generator(llm),
        verifier=Verifier(Judge(llm)),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.verifier = DevTokenVerifier(DEV_KEY, settings.auth_audience)
    app.state.users = UserDirectory(sessions)
    app.state.pipeline = Pipeline(services)
    token = mint_dev_token(DEV_KEY, subject=SUBJECT, audience=settings.auth_audience)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/ask",
            json={"question": "Dumbbell versus barbell curls for biceps growth?"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    assert response.status_code == 200
    events = parse(response.text)
    order: list[str] = []
    for name, _ in events:
        if name != "step" and (not order or order[-1] != name):
            order.append(name)
    assert order == ["gap", "token", "citations", "evidence_table", "rating", "done"]
    citations = next(data for name, data in events if name == "citations")
    assert citations[0]["pmid"] in BICEPS_PMIDS

    async with sessions() as session:
        user = (
            await session.execute(
                select(User).where(User.auth_issuer == DEV_ISSUER, User.auth_subject == SUBJECT)
            )
        ).scalar_one()
    assert user.id is not None
