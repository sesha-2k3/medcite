"""Fake pipeline services: a fixed plan, scripted searches, recorded ingestion, and a
retriever that returns prepared passages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any

from app.config import Settings
from app.db.queries.discovery import DiscoveryArticle
from app.db.queries.query_logs import CompletedQuery
from app.discovery.service import DiscoveryConfig, DiscoveryService
from app.generation.generate import Generator
from app.generation.plan import DateWindow, Plan, Tier
from app.generation.verify import Judge, Verifier
from app.ingest.metadata import OutcomeClass
from app.ingest.service import IngestReport
from app.models.llm import LLMClient, UsageMeter
from app.retrieval.retriever import EvidencePassage
from app.services import PipelineConfig, Resources, Services
from app.sources.pubmed import SearchResult, Sort
from tests.generation.builders import QUESTION, creatine_passages
from tests.llm_fakes import Reply, ScriptedProvider

PRIMARY = [90_000_001, 90_000_002]  # the trial and the cohort: no synthesis in tier 1
ALL = [*PRIMARY, 90_000_003]


def found_by_search(candidates: Mapping[int, int]) -> list[EvidencePassage]:
    """The creatine passages, tagged with the tier that found each article."""
    return [replace(p, tier=candidates.get(p.scored.chunk.pmid)) for p in creatine_passages()]


def question_plan(**changes: Any) -> Plan:
    tiers = [
        Tier(level=level, purpose=purpose, pubmed_query=f"q{level}")
        for level, purpose in enumerate(
            ["exact evidence", "direct primary studies", "related syntheses", "surrogate"],
            start=1,
        )
    ]
    return Plan(mode="question", standalone_query=QUESTION, tiers=tiers).model_copy(update=changes)


class FakePlanner:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    async def plan(
        self,
        question: str,
        *,
        history: Sequence[str] = (),
        today: date | None = None,
        window: DateWindow | None = None,
        meter: UsageMeter | None = None,
    ) -> Plan:
        return self._plan


class FakeSearcher:
    """Returns ``pmids_by_query[term]`` (nothing for other queries) and records each term."""

    def __init__(
        self, pmids_by_query: Mapping[str, list[int]], counts: Mapping[str, int] | None = None
    ) -> None:
        self._pmids = pmids_by_query
        self._counts = counts or {}
        self.terms: list[str] = []
        self.calls: list[dict[str, Any]] = []

    async def esearch(
        self,
        term: str,
        *,
        retmax: int = 100,
        sort: Sort = "relevance",
        cache_ttl_s: int | None = None,
    ) -> SearchResult:
        self.terms.append(term)
        self.calls.append({"term": term, "retmax": retmax, "sort": sort, "ttl": cache_ttl_s})
        found = self._pmids.get(term, [])
        count = self._counts.get(term, len(found))
        return SearchResult(count=count, pmids=found[:retmax], query_translation=None)


class FakeIngester:
    def __init__(self) -> None:
        self.batches: list[list[int]] = []

    async def ingest(
        self, pmids: Sequence[int], *, refresh: bool = False, meter: UsageMeter | None = None
    ) -> IngestReport:
        self.batches.append(list(pmids))
        return IngestReport(requested=len(pmids), already_stored=0)


class FakeRetriever:
    def __init__(self, passages: Callable[[Mapping[int, int]], list[EvidencePassage]]) -> None:
        self._passages = passages
        self.calls: list[dict[int, int]] = []
        self.caches: list[dict[int, float] | None] = []

    async def retrieve(
        self,
        question: str,
        *,
        candidate_tiers: Mapping[int, int] | None = None,
        question_outcome: OutcomeClass | None = None,
        score_cache: dict[int, float] | None = None,
    ) -> list[EvidencePassage]:
        self.calls.append(dict(candidate_tiers or {}))
        self.caches.append(score_cache)
        return self._passages(candidate_tiers or {})


class FakeRecorder:
    def __init__(self, query_id: int = 42, error: Exception | None = None) -> None:
        self.entries: list[CompletedQuery] = []
        self._query_id = query_id
        self._error = error

    async def record(self, entry: CompletedQuery) -> int:
        self.entries.append(entry)
        if self._error is not None:
            raise self._error
        return self._query_id


class FakeArticleSource:
    def __init__(self, articles: list[DiscoveryArticle]) -> None:
        self._by_pmid = {a.passage.pmid: a for a in articles}

    async def load(self, pmids: Sequence[int]) -> list[DiscoveryArticle]:
        return [self._by_pmid[p] for p in pmids if p in self._by_pmid]


def fake_services(
    settings: Settings,
    replies: list[Reply],
    *,
    plan: Plan | None = None,
    searcher: FakeSearcher | None = None,
    retriever: FakeRetriever | None = None,
    ingester: FakeIngester | None = None,
    config: PipelineConfig | None = None,
    recorder: FakeRecorder | None = None,
    discovery_articles: list[DiscoveryArticle] | None = None,
) -> tuple[Services, ScriptedProvider]:
    """With ``discovery_articles``, discovery runs against them; otherwise it is off."""
    provider = ScriptedProvider(replies)
    llm = LLMClient(settings, provider)
    services = Services(
        planner=FakePlanner(plan or question_plan()),
        searcher=searcher or FakeSearcher({}),
        ingester=ingester or FakeIngester(),
        retriever=retriever or FakeRetriever(lambda _: []),
        generator=Generator(llm),
        verifier=Verifier(Judge(llm)),
        config=config or PipelineConfig(),
        recorder=recorder,
        discovery=DiscoveryService(
            llm, FakeArticleSource(discovery_articles), Judge(llm), DiscoveryConfig()
        )
        if discovery_articles is not None
        else None,
    )
    return services, provider


def factory(services: Services) -> Callable[[Settings, Resources], Any]:
    async def _make(settings: Settings, resources: Resources) -> Services:
        return services

    return _make
