"""Synthetic discovery articles in two topics, with vectors that separate them cleanly."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import numpy.typing as npt

from app.db.models import EMBEDDING_DIM
from app.db.queries.chunks import ChunkView
from app.db.queries.discovery import DiscoveryArticle
from app.ingest.metadata import Design, StudyMetadata

TOPIC = "diabetes in older adults"
GLUCOSE = [90_000_301, 90_000_302, 90_000_303]
MUSCLE = [90_000_304, 90_000_305, 90_000_306]
ARTICLES: dict[int, tuple[str, str, Design, int | None]] = {
    90_000_301: (
        "Continuous glucose monitoring in adults over 75: a randomized trial",
        "Continuous glucose monitoring reduced time in hypoglycemia by 2.1 hours per week "
        "in adults over 75.",
        "rct",
        120,
    ),
    90_000_302: (
        "Glucose monitor use and emergency visits in older adults",
        "Monitor use was associated with fewer emergency visits for hypoglycemia "
        "(hazard ratio 0.71).",
        "cohort",
        850,
    ),
    90_000_303: (
        "Glucose monitoring technology for older people with diabetes",
        "Continuous glucose monitoring is increasingly used in older adults with diabetes.",
        "narrative_review",
        None,
    ),
    90_000_304: (
        "Lean mass loss with GLP-1 receptor agonists in older adults",
        "GLP-1 receptor agonist users lost more lean mass than non-users.",
        "cohort",
        400,
    ),
    90_000_305: (
        "Resistance training during GLP-1 treatment: a pilot trial",
        "Resistance training preserved muscle strength during GLP-1 treatment.",
        "rct",
        60,
    ),
    90_000_306: (
        "Sarcopenia in older adults with diabetes: a systematic review",
        "Sarcopenia was more common in older adults with diabetes across 12 studies.",
        "systematic_review",
        None,
    ),
}


def unit(*weights: tuple[int, float]) -> np.ndarray:
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    for axis, weight in weights:
        vector[axis] = weight
    return (vector / np.linalg.norm(vector)).astype(np.float32)


# Within each topic the first article is the most central, then the other two tie.
VECTORS = {
    90_000_301: unit((0, 1.0)),
    90_000_302: unit((0, 1.0), (2, 0.1)),
    90_000_303: unit((0, 1.0), (2, -0.1)),
    90_000_304: unit((1, 1.0)),
    90_000_305: unit((1, 1.0), (3, 0.1)),
    90_000_306: unit((1, 1.0), (3, -0.1)),
}


def shared_direction_batch(
    topics: int = 4, per_topic: int = 6, seed: int = 0
) -> npt.NDArray[np.float32]:
    """Topics on top of one dominant shared direction, as with real MedCPT abstracts."""
    rng = np.random.default_rng(seed)
    shared = rng.normal(size=EMBEDDING_DIM)
    directions = [rng.normal(size=EMBEDDING_DIM) for _ in range(topics)]
    rows = []
    for index in range(topics * per_topic):
        topic = directions[index % topics]
        row = 3.0 * shared / np.linalg.norm(shared) + 0.6 * topic / np.linalg.norm(topic)
        row = row + rng.normal(0, 0.03, EMBEDDING_DIM)
        rows.append(row / np.linalg.norm(row))
    return np.stack(rows).astype(np.float32)


def article(pmid: int, *, vector: np.ndarray | None = None) -> DiscoveryArticle:
    title, content, design, n = ARTICLES[pmid]
    chunk = ChunkView(
        chunk_id=pmid - 90_000_000,
        article_id=pmid - 90_000_000,
        pmid=pmid,
        section="Abstract-CONCLUSIONS",
        chunk_index=1,
        content=content,
        title=title,
        journal=None,
        pub_date=date(2026, 6, 1),
        pub_types=(),
        publication_status=None,
        study_meta=StudyMetadata(design=design, sample_size=n),
    )
    return DiscoveryArticle(passage=chunk, vector=VECTORS[pmid] if vector is None else vector)


def all_articles() -> list[DiscoveryArticle]:
    return [article(pmid) for pmid in [*GLUCOSE, *MUSCLE]]


class FakeArticleSource:
    def __init__(self, articles: list[DiscoveryArticle]) -> None:
        self._by_pmid = {a.passage.pmid: a for a in articles}
        self.requests: list[list[int]] = []

    async def load(self, pmids: Sequence[int]) -> list[DiscoveryArticle]:
        wanted = list(pmids)
        self.requests.append(wanted)
        return [self._by_pmid[p] for p in wanted if p in self._by_pmid]
