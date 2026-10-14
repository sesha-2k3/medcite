"""Abstract ingestion: PMIDs in, stored and embedded articles out.

fetch (EFetch) → parse → metadata (LLM) → chunk → embed (MedCPT) → store
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.ingest.chunker import chunk_article
from app.ingest.metadata import StudyMetadata
from app.ingest.pubmed_parser import ParsedArticle, parse_pubmed_xml
from app.ingest.store import ArticleRecord, ArticleStore, StoreOutcome
from app.models.embeddings import Embedder
from app.models.llm import UsageMeter

logger = logging.getLogger(__name__)


class RecordFetcher(Protocol):
    async def efetch(self, pmids: Sequence[int]) -> list[str]: ...


class MetadataSource(Protocol):
    async def extract(
        self, articles: Sequence[ParsedArticle], meter: UsageMeter | None = None
    ) -> dict[int, StudyMetadata]: ...


@dataclass(frozen=True, slots=True)
class IngestReport:
    requested: int
    already_stored: int
    not_returned: list[int] = field(default_factory=list)
    outcomes: dict[StoreOutcome, int] = field(default_factory=dict)

    @property
    def stored(self) -> int:
        return self.outcomes.get(StoreOutcome.INSERTED, 0) + self.outcomes.get(
            StoreOutcome.UPDATED, 0
        )


class IngestService:
    def __init__(
        self,
        fetcher: RecordFetcher,
        extractor: MetadataSource,
        embedder: Embedder,
        store: ArticleStore,
    ) -> None:
        self._fetcher = fetcher
        self._extractor = extractor
        self._embedder = embedder
        self._store = store

    async def ingest(
        self, pmids: Sequence[int], *, refresh: bool = False, meter: UsageMeter | None = None
    ) -> IngestReport:
        """Ingest PMIDs not yet stored (all of them with ``refresh``)."""
        wanted = list(dict.fromkeys(pmids))
        existing = set() if refresh else await self._store.existing_pmids(wanted)
        missing = [pmid for pmid in wanted if pmid not in existing]

        documents = await self._fetcher.efetch(missing)
        articles = [article for doc in documents for article in parse_pubmed_xml(doc)]
        returned = {a.pmid for a in articles}
        live = [a for a in articles if not a.is_retracted]
        metadata = await self._extractor.extract(live, meter)
        records = await asyncio.to_thread(self._build_records, articles, metadata)

        outcomes: Counter[StoreOutcome] = Counter()
        for record in records:
            outcomes[await self._store.save(record)] += 1

        report = IngestReport(
            requested=len(wanted),
            already_stored=len(existing),
            not_returned=[pmid for pmid in missing if pmid not in returned],
            outcomes=dict(outcomes),
        )
        logger.info(
            "ingest_done",
            extra={
                "requested": report.requested,
                "already_stored": report.already_stored,
                "not_returned": len(report.not_returned),
                **{outcome.value: count for outcome, count in outcomes.items()},
            },
        )
        return report

    def _build_records(
        self, articles: Sequence[ParsedArticle], metadata: dict[int, StudyMetadata]
    ) -> list[ArticleRecord]:
        """Chunk and embed every live article in one encoder pass (CPU-bound; runs in a thread)."""
        drafts = {
            a.pmid: chunk_article(a, metadata[a.pmid].design)
            for a in articles
            if not a.is_retracted
        }
        flat = [chunk for chunks in drafts.values() for chunk in chunks]
        vectors = self._embedder.embed_articles([(c.embed_title, c.embed_text) for c in flat])

        records: list[ArticleRecord] = []
        offset = 0
        for article in articles:
            chunks = drafts.get(article.pmid, [])
            records.append(
                ArticleRecord(
                    article=article,
                    metadata=metadata.get(article.pmid),
                    chunks=chunks,
                    embeddings=vectors[offset : offset + len(chunks)] if chunks else None,
                    embed_model=self._embedder.article_model,
                )
            )
            offset += len(chunks)
        return records
