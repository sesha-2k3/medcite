"""Fakes for the ingestion pipeline's collaborators."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from lxml import etree

from app.db.models import EMBEDDING_DIM
from app.ingest.metadata import StudyMetadata, fallback_metadata
from app.ingest.pubmed_parser import ParsedArticle
from app.models.embeddings import Vectors
from app.models.llm import UsageMeter


class FixtureFetcher:
    """Serves only the requested records out of one EFetch fixture file."""

    def __init__(self, path: Path) -> None:
        self._root = etree.fromstring(path.read_bytes())
        self.requests: list[list[int]] = []

    async def efetch(self, pmids: Sequence[int]) -> list[str]:
        self.requests.append(list(pmids))
        if not pmids:
            return []
        wanted = {str(p) for p in pmids}
        subset = etree.Element("PubmedArticleSet")
        for node in self._root.iterfind("PubmedArticle"):
            if node.findtext("MedlineCitation/PMID") in wanted:
                subset.append(etree.fromstring(etree.tostring(node)))
        return [etree.tostring(subset, encoding="unicode")]


class HashEmbedder:
    """Deterministic unit vectors derived from the input text."""

    article_model = "fake/hash-embedder"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed_articles(self, pairs: Sequence[tuple[str, str]]) -> Vectors:
        self.calls.append(len(pairs))
        return self._vectors([f"{title}\n{text}" for title, text in pairs])

    def embed_queries(self, texts: Sequence[str]) -> Vectors:
        return self._vectors(list(texts))

    @staticmethod
    def _vectors(texts: list[str]) -> Vectors:
        out = np.empty((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            vec = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM)
            out[row] = vec / np.linalg.norm(vec)
        return out


class PubTypeMetadata:
    """Metadata from publication types only, as when the LLM is unavailable."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def extract(
        self, articles: Sequence[ParsedArticle], meter: UsageMeter | None = None
    ) -> dict[int, StudyMetadata]:
        self.seen.extend(a.pmid for a in articles)
        return {a.pmid: fallback_metadata(a) for a in articles}
