from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.queries.chunks import ChunkRepository
from app.ingest.metadata import StudyMetadata
from app.retrieval.diversity import DiversityLimits
from app.retrieval.hybrid_search import HybridSearcher
from app.retrieval.retriever import RetrievalConfig, Retriever
from tests.ingest.fakes import HashEmbedder
from tests.retrieval.seed import seed, unit_vector

pytestmark = pytest.mark.integration

BIG_STUDY = 90002000


class KeywordScorer:
    """Relevance = how many times 'biceps' appears in the passage."""

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [float(p.lower().count("biceps")) for p in passages]


@pytest.fixture
async def corpus(sessions: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    big = [
        (f"Abstract-SECTION{i}", "biceps " * (10 - i) + "training", unit_vector(i))
        for i in range(5)
    ]
    await seed(
        sessions,
        BIG_STUDY,
        "A large biceps trial",
        big,
        StudyMetadata(design="rct", sample_size=24, outcome_class="physiological"),
    )
    for n in range(1, 13):
        await seed(
            sessions,
            BIG_STUDY + n,
            f"Study {n}",
            [("Abstract", "biceps growth " + "filler " * n, unit_vector(100 + n))],
            StudyMetadata(design="mechanistic", outcome_class="surrogate"),
        )
    return sessions


def build(sessions: async_sessionmaker[AsyncSession], **limits: int) -> Retriever:
    return Retriever(
        HybridSearcher(sessions),
        ChunkRepository(sessions),
        HashEmbedder(),
        KeywordScorer(),
        RetrievalConfig(limits=DiversityLimits(**limits)),
    )


async def test_caps_grades_and_tiers(corpus: async_sessionmaker[AsyncSession]) -> None:
    passages = await build(corpus, max_keep=15, per_article=3).retrieve(
        "biceps growth",
        candidate_tiers={BIG_STUDY: 2, BIG_STUDY + 1: 4},
        question_outcome="physiological",
    )
    per_article = Counter(p.scored.chunk.pmid for p in passages)

    assert len(passages) == 15
    assert per_article[BIG_STUDY] == 3
    assert [p.scored.relevance for p in passages] == sorted(
        (p.scored.relevance for p in passages), reverse=True
    )

    big = next(p for p in passages if p.scored.chunk.pmid == BIG_STUDY)
    assert (big.grade.badge, big.grade.modifiers, big.tier) == ("RCT", ("small_sample",), 2)
    mechanistic = next(p for p in passages if p.scored.chunk.pmid == BIG_STUDY + 1)
    assert mechanistic.grade.badge == "Mechanistic"
    assert "surrogate_outcome" in mechanistic.grade.modifiers
    assert mechanistic.tier == 4
    assert all(p.tier is None for p in passages if p.scored.chunk.pmid > BIG_STUDY + 1)


async def test_whole_index_search_works_without_candidates(
    corpus: async_sessionmaker[AsyncSession],
) -> None:
    passages = await build(corpus, max_keep=5).retrieve("biceps")
    assert len(passages) == 5
    assert passages[0].scored.chunk.pmid == BIG_STUDY


async def test_empty_index_returns_nothing(sessions: async_sessionmaker[AsyncSession]) -> None:
    assert await build(sessions).retrieve("anything at all") == []
