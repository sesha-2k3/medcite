"""Wiring for the ingestion service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingest.metadata import MetadataExtractor
from app.ingest.service import IngestService, RecordFetcher
from app.ingest.store import ArticleStore
from app.models.embeddings import Embedder
from app.models.llm import LLMClient


def build_ingest_service(
    settings: Settings,
    fetcher: RecordFetcher,
    llm: LLMClient,
    embedder: Embedder,
    sessions: async_sessionmaker[AsyncSession],
) -> IngestService:
    return IngestService(
        fetcher=fetcher,
        extractor=MetadataExtractor(llm, batch_size=settings.metadata_batch_size),
        embedder=embedder,
        store=ArticleStore(sessions),
    )
