"""Everything the pipeline needs, built once per process in the app's lifespan.

Tests pass their own ``ServicesFactory`` with fakes, so they never load MedCPT.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.queries.discovery import DiscoveryArticles
from app.db.queries.query_logs import CompletedQuery, QueryLogRepository
from app.discovery.service import DiscoveryService, discovery_config
from app.generation.context import ContextBudget
from app.generation.factory import context_budget
from app.generation.generate import Generator
from app.generation.plan import DateWindow, Plan, Planner
from app.generation.service import AnswerGenerator, AnswerVerifier
from app.generation.verify import Judge, Verifier
from app.ingest.factory import build_ingest_service
from app.ingest.metadata import OutcomeClass
from app.ingest.service import IngestReport
from app.models.cross_encoder import load_cross_encoder
from app.models.embeddings import load_embedder
from app.models.llm import UsageMeter, build_llm_client
from app.retrieval.factory import build_retriever
from app.retrieval.retriever import EvidencePassage
from app.sources.factory import build_pubmed_client
from app.sources.pubmed import SearchResult, Sort


class QuestionPlanner(Protocol):
    async def plan(
        self,
        question: str,
        *,
        history: Sequence[str] = (),
        today: date | None = None,
        window: DateWindow | None = None,
        meter: UsageMeter | None = None,
    ) -> Plan: ...


class Searcher(Protocol):
    async def esearch(
        self,
        term: str,
        *,
        retmax: int = 100,
        sort: Sort = "relevance",
        cache_ttl_s: int | None = None,
    ) -> SearchResult: ...


class Ingester(Protocol):
    async def ingest(
        self, pmids: Sequence[int], *, refresh: bool = False, meter: UsageMeter | None = None
    ) -> IngestReport: ...


class PassageRetriever(Protocol):
    async def retrieve(
        self,
        question: str,
        *,
        candidate_tiers: Mapping[int, int] | None = None,
        question_outcome: OutcomeClass | None = None,
        score_cache: dict[int, float] | None = None,
    ) -> list[EvidencePassage]: ...


class QueryRecorder(Protocol):
    async def record(self, entry: CompletedQuery) -> int: ...


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    tier_retmax: int = 50
    enough_relevant_passages: int = 5
    relevance_threshold: float = 0.0
    budget: ContextBudget = field(default_factory=ContextBudget)


@dataclass(frozen=True, slots=True)
class Services:
    planner: QuestionPlanner
    searcher: Searcher
    ingester: Ingester
    retriever: PassageRetriever
    generator: AnswerGenerator
    verifier: AnswerVerifier
    config: PipelineConfig
    recorder: QueryRecorder | None = None
    discovery: DiscoveryService | None = None


@dataclass(frozen=True, slots=True)
class Resources:
    http: httpx.AsyncClient
    redis: Redis
    sessions: async_sessionmaker[AsyncSession]


ServicesFactory = Callable[[Settings, Resources], Awaitable[Services | None]]


def pipeline_config(settings: Settings) -> PipelineConfig:
    return PipelineConfig(
        tier_retmax=settings.tier_retmax,
        enough_relevant_passages=settings.enough_relevant_passages,
        relevance_threshold=settings.relevance_threshold,
        budget=context_budget(settings),
    )


async def build_services(settings: Settings, resources: Resources) -> Services:
    """Loads MedCPT (about 2 GB) once; blocking loads run in a thread."""
    embedder = await asyncio.to_thread(load_embedder, settings)
    scorer = await asyncio.to_thread(load_cross_encoder, settings)
    llm = build_llm_client(settings)
    pubmed = build_pubmed_client(settings, resources.http, resources.redis)
    return Services(
        planner=Planner(llm, pubmed),
        searcher=pubmed,
        ingester=build_ingest_service(settings, pubmed, llm, embedder, resources.sessions),
        retriever=build_retriever(settings, resources.sessions, embedder, scorer),
        generator=Generator(llm, max_tokens=settings.generation_max_tokens),
        verifier=Verifier(Judge(llm, max_tokens=settings.judge_max_tokens)),
        config=pipeline_config(settings),
        recorder=QueryLogRepository(resources.sessions),
        discovery=DiscoveryService(
            llm,
            DiscoveryArticles(resources.sessions),
            Judge(llm, max_tokens=settings.judge_max_tokens),
            discovery_config(settings),
        ),
    )


async def no_services(settings: Settings, resources: Resources) -> None:
    """For an API that serves only health checks (tests): /v1/ask answers 503."""
    return None
