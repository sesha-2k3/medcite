"""Fake pipeline services: a fixed plan, scripted searches, recorded ingestion, and a
retriever that returns prepared passages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from app.config import Settings
from app.generation.generate import Generator
from app.generation.plan import DateWindow, Plan, Tier
from app.generation.verify import Judge, Verifier
from app.ingest.metadata import OutcomeClass
from app.ingest.service import IngestReport
from app.models.llm import LLMClient, UsageMeter
from app.retrieval.retriever import EvidencePassage
from app.services import PipelineConfig, Resources, Services
from app.sources.pubmed import SearchResult
from tests.generation.builders import QUESTION
from tests.llm_fakes import Reply, ScriptedProvider


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

    def __init__(self, pmids_by_query: Mapping[str, list[int]]) -> None:
        self._pmids = pmids_by_query
        self.terms: list[str] = []

    async def esearch(self, term: str, *, retmax: int = 100) -> SearchResult:
        self.terms.append(term)
        pmids = self._pmids.get(term, [])[:retmax]
        return SearchResult(count=len(pmids), pmids=pmids, query_translation=None)


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

    async def retrieve(
        self,
        question: str,
        *,
        candidate_tiers: Mapping[int, int] | None = None,
        question_outcome: OutcomeClass | None = None,
    ) -> list[EvidencePassage]:
        self.calls.append(dict(candidate_tiers or {}))
        return self._passages(candidate_tiers or {})


def fake_services(
    settings: Settings,
    replies: list[Reply],
    *,
    plan: Plan | None = None,
    searcher: FakeSearcher | None = None,
    retriever: FakeRetriever | None = None,
    ingester: FakeIngester | None = None,
    config: PipelineConfig | None = None,
) -> tuple[Services, ScriptedProvider]:
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
    )
    return services, provider


def factory(services: Services) -> Callable[[Settings, Resources], Any]:
    async def _make(settings: Settings, resources: Resources) -> Services:
        return services

    return _make
