"""Discovery on Postgres: ingestion, article vectors from stored embeddings, clustering,
naming, summaries and verification, with scripted models.

The acceptance test runs on a recording of a real discovery search; it is skipped until
tests/fixtures/pubmed/minimized/efetch_discovery_diabetes.xml exists (see its docstring).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import EMBEDDING_DIM, Article
from app.db.queries.discovery import DiscoveryArticles
from app.discovery.digest import Digest
from app.discovery.service import DiscoveryConfig, DiscoveryService, Grouping
from app.discovery.themes import build_contexts
from app.generation.answer import VerificationResult
from app.generation.verify import Judge
from app.ingest.service import IngestService
from app.ingest.store import ArticleStore
from app.models.llm import LLMClient
from tests.discovery.builders import GLUCOSE, MUSCLE, TOPIC
from tests.ingest.fakes import FixtureFetcher, PubTypeMetadata
from tests.llm_fakes import ScriptedProvider

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pubmed"
SYNTHETIC = FIXTURES / "efetch_discovery_synthetic.xml"
RECORDED = FIXTURES / "minimized" / "efetch_discovery_diabetes.xml"
JUDGE_EMPTY = json.dumps({"claims": [], "gap_statement_ok": None, "conflict_omitted": False})
TOPIC_WORDS = (
    ("glucose", "hypoglycemia", "monitor"),
    ("muscle", "lean", "sarcopenia", "glp-1"),
)
# Clinical concept families for the recorded titles; stems match inside longer words.
CONCEPTS = (
    ("glyc", "hba1c", "metformin", "insulin", "glucose", "glp-1"),
    ("cardi", "heart", "vascular", "hypertens", "stroke"),
    ("exercise", "physical activity", "fitness", "frailty", "sarcopenia", "muscle"),
    ("technology", "experience", "living", "phenomenolog", "education", "self-management"),
)


class TopicEmbedder:
    """Puts each synthetic topic on its own axis, plus a little deterministic noise."""

    article_model = "fake/topic-embedder"

    def embed_articles(self, pairs: Sequence[tuple[str, str]]) -> np.ndarray:
        return np.stack([self._vector(f"{title} {text}") for title, text in pairs])

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vector(text) for text in texts])

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        lower = text.lower()
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        vector = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM) * 0.01
        for axis, words in enumerate(TOPIC_WORDS):
            vector[axis] += sum(lower.count(word) for word in words)
        return (vector / np.linalg.norm(vector)).astype(np.float32)


class ConceptEmbedder:
    """Places each title on a few clinical concept axes.

    Titles only, because minimized fixtures replace abstract prose with identical
    placeholders, and concepts rather than raw words, because nearly every result of the
    search shares its own terms ("diabetes", "older adults"). The noise is small enough that
    one concept word outweighs it.
    """

    article_model = "fake/concept-embedder"

    def embed_articles(self, pairs: Sequence[tuple[str, str]]) -> np.ndarray:
        return np.stack([self._vector(title) for title, _ in pairs])

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vector(text) for text in texts])

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        lower = text.lower()
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        vector = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM) * 0.001
        for axis, stems in enumerate(CONCEPTS):
            vector[axis] += sum(lower.count(stem) for stem in stems)
        return (vector / np.linalg.norm(vector)).astype(np.float32)


def pmids_in(path: Path) -> list[int]:
    return [int(p) for p in re.findall(r'<PMID Version="1">(\d+)</PMID>', path.read_text())]


async def ingest(
    sessions: async_sessionmaker[AsyncSession],
    path: Path,
    embedder: TopicEmbedder | ConceptEmbedder,
) -> list[int]:
    pmids = pmids_in(path)
    store = ArticleStore(sessions)
    service = IngestService(FixtureFetcher(path), PubTypeMetadata(), embedder, store)
    await service.ingest(pmids)
    return pmids


def summaries_for(grouping: Grouping, per_theme: int) -> list[str]:
    """One cited, number-free summary per theme, using the markers code will assign."""
    replies = []
    for context in build_contexts(grouping.drafts, per_theme):
        first, second = context.items[0].marker, context.items[1].marker
        text = (
            f"Recent articles in this theme report related findings [{first}, {second}]. "
            f"The evidence comes from a mix of study designs [{first}]."
        )
        replies.append(json.dumps({"summary": text}))
    return replies


def naming_reply(count: int) -> str:
    themes = [{"clusters": [f"K{i}"], "name": f"Named theme {i}"} for i in range(1, count + 1)]
    return json.dumps({"themes": themes})


async def run_digest(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pmids: list[int],
    config: DiscoveryConfig,
) -> tuple[Digest, VerificationResult]:
    grouping_llm = LLMClient(settings, ScriptedProvider([naming_reply(config.max_themes)]))
    grouper = DiscoveryService(grouping_llm, DiscoveryArticles(sessions), None, config)
    grouping = await grouper.group(TOPIC, pmids)
    replies = [*summaries_for(grouping, config.articles_per_theme), JUDGE_EMPTY]
    llm = LLMClient(settings, ScriptedProvider(replies))
    writer = DiscoveryService(llm, DiscoveryArticles(sessions), Judge(llm), config)
    return await writer.summarize(TOPIC, grouping, found=len(pmids))


@pytest.fixture
async def recorded_sessions(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """The recording holds real PMIDs, which the shared fixture does not clear."""
    pmids = pmids_in(RECORDED)

    async def clear() -> None:
        async with sessions.begin() as session:
            await session.execute(delete(Article).where(Article.pmid.in_(pmids)))

    await clear()
    yield sessions
    await clear()


async def test_article_vectors_and_passages_come_from_stored_chunks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    pmids = await ingest(sessions, SYNTHETIC, TopicEmbedder())
    articles = await DiscoveryArticles(sessions).load([*pmids, 1])
    assert [a.passage.pmid for a in articles] == pmids
    assert all("CONCLUSIONS" in (a.passage.section or "") for a in articles)
    assert all(abs(float(np.linalg.norm(a.vector)) - 1.0) < 1e-5 for a in articles)


async def test_synthetic_digest_has_two_themes_with_cited_articles(
    sessions: async_sessionmaker[AsyncSession], make_settings: Callable[..., Settings]
) -> None:
    pmids = await ingest(sessions, SYNTHETIC, TopicEmbedder())
    digest, result = await run_digest(sessions, make_settings(), pmids, DiscoveryConfig())
    themes = digest.themes
    assert len(themes) == 2
    groups = [{a.pmid for a in theme.articles} for theme in themes]
    assert sorted(map(sorted, groups)) == [GLUCOSE, MUSCLE]
    assert all(a.badge for theme in themes for a in theme.articles)
    assert result.passed


@pytest.mark.skipif(not RECORDED.exists(), reason="record efetch_discovery_diabetes.xml first")
async def test_recorded_diabetes_digest_has_named_themes(
    recorded_sessions: async_sessionmaker[AsyncSession], make_settings: Callable[..., Settings]
) -> None:
    """Acceptance (handoff M8). To record the fixture:

    make pubmed-record q='("Diabetes Mellitus"[mh] OR diabetes[tiab]) AND ("Aged"[mh] OR
      "older adults"[tiab]) AND hasabstract AND 2026/04/14:3000[edat]' n=12
    mv tests/fixtures/pubmed/recorded/efetch-<hash>.xml \\
       tests/fixtures/pubmed/recorded/efetch_discovery_diabetes.xml
    make fixtures-minimize
    """
    pmids = await ingest(recorded_sessions, RECORDED, ConceptEmbedder())
    digest, result = await run_digest(recorded_sessions, make_settings(), pmids, DiscoveryConfig())
    themes = digest.themes
    loaded = await DiscoveryArticles(recorded_sessions).load(pmids)
    titles = "\n".join(a.passage.title for a in loaded)
    assert len(themes) >= 2, f"too few themes from these titles:\n{titles}"
    for theme in themes:
        assert theme.name.startswith("Named theme")
        cited = set(re.findall(r"C\d+", theme.summary))
        assert len(cited) >= 2
        assert all(a.badge for a in theme.articles)
    assert result.passed
