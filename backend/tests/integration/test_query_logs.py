"""Query logs, feedback ownership, daily usage and article reads on Postgres."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.tokens import Claims
from app.auth.users import User, UserDirectory
from app.db.queries.articles import ArticleRepository
from app.db.queries.query_logs import CompletedQuery, QueryLogRepository, UsageRepository
from app.ingest.service import IngestService
from app.ingest.store import ArticleStore
from tests.ingest.fakes import FixtureFetcher, HashEmbedder, PubTypeMetadata
from tests.ingest.helpers import EDGE_CASES

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 14)


async def new_user(sessions: async_sessionmaker[AsyncSession]) -> User:
    claims = Claims(issuer="https://issuer.test", subject=uuid.uuid4().hex, email=None)
    return await UserDirectory(sessions).resolve(claims)


def entry(user: User) -> CompletedQuery:
    return CompletedQuery(
        user_id=user.id,
        mode="question",
        user_query="Does creatine help?",
        plan={"mode": "question"},
        candidate_pmids=[90_000_001],
        context_chunk_ids=[1, 2],
        answer={"direct_answer": "Mixed."},
        evidence_gap=False,
        verification={"passed": True},
        latency_ms=1234,
        cost_usd=Decimal("0.00120"),
        trace_id="t" * 32,
    )


async def test_feedback_only_from_the_user_who_asked(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner, other = await new_user(sessions), await new_user(sessions)
    logs = QueryLogRepository(sessions)
    query_id = await logs.record(entry(owner))
    assert await logs.set_feedback(query_id, other.id, 1) is False
    assert await logs.set_feedback(query_id, owner.id, -1) is True
    assert await logs.set_feedback(query_id + 10_000, owner.id, 1) is False


async def test_usage_accumulates_per_day(sessions: async_sessionmaker[AsyncSession]) -> None:
    user = await new_user(sessions)
    usage = UsageRepository(sessions)
    assert await usage.cost_on(user.id, DAY) == Decimal(0)
    await usage.add(user.id, DAY, Decimal("0.01000"))
    await usage.add(user.id, DAY, Decimal("0.00500"))
    assert await usage.cost_on(user.id, DAY) == Decimal("0.01500")
    assert await usage.cost_on(user.id, date(2026, 10, 15)) == Decimal(0)


async def test_article_reads_skip_title_only_chunks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestService(
        FixtureFetcher(EDGE_CASES), PubTypeMetadata(), HashEmbedder(), ArticleStore(sessions)
    )
    await service.ingest([90_000_101, 90_000_102, 90_000_103])
    articles = ArticleRepository(sessions)
    structured = await articles.get(90_000_101)
    assert structured is not None
    assert structured.passages
    assert all(p.section != "Title" for p in structured.passages)
    assert [p.chunk_index for p in structured.passages] == sorted(
        p.chunk_index or 0 for p in structured.passages
    )
    assert await articles.get(1) is None
