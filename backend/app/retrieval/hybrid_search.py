"""Hybrid retrieval over chunks: pgvector cosine distance (dense) and Postgres full-text
search (sparse), fused with reciprocal rank fusion in a single query.

Title-only chunks are never returned: they keep articles findable but are not evidence.
Retracted articles have no chunks (the store deletes them), so no retraction filter is needed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import EMBEDDING_DIM
from app.ingest.chunker import TITLE_ONLY_SECTION
from app.models.embeddings import Vectors

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
_OPERATOR_WORDS = frozenset({"or", "and", "not"})

# Candidate-restricted: the pool is materialized, so ordering it by distance is an exact scan.
# HNSW with a selective filter can silently return fewer rows than asked for.
_CANDIDATE_SQL = """
WITH pool AS MATERIALIZED (
    SELECT c.id, c.embedding, c.tsv
    FROM chunks c JOIN articles a ON a.id = c.article_id
    WHERE a.pmid = ANY(:pmids) AND c.section <> :title_section AND c.embedding IS NOT NULL
),
dense AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :query_vec, id) AS r
    FROM pool ORDER BY embedding <=> :query_vec, id LIMIT :per_leg
),
sparse AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC, id) AS r
    FROM pool, websearch_to_tsquery('english', :query_text) q
    WHERE tsv @@ q
    ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT :per_leg
)
SELECT COALESCE(d.id, s.id) AS chunk_id, d.r AS dense_rank, s.r AS sparse_rank,
       COALESCE(1.0 / (:k + d.r), 0) + COALESCE(1.0 / (:k + s.r), 0) AS rrf_score
FROM dense d FULL OUTER JOIN sparse s ON s.id = d.id
ORDER BY rrf_score DESC, chunk_id
LIMIT :limit
"""

# Whole index: the dense leg reads chunks directly so Postgres can use the HNSW index.
_INDEX_SQL = """
WITH dense AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :query_vec, id) AS r
    FROM chunks
    WHERE section <> :title_section AND embedding IS NOT NULL
    ORDER BY embedding <=> :query_vec, id LIMIT :per_leg
),
sparse AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC, id) AS r
    FROM chunks, websearch_to_tsquery('english', :query_text) q
    WHERE tsv @@ q AND section <> :title_section
    ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT :per_leg
)
SELECT COALESCE(d.id, s.id) AS chunk_id, d.r AS dense_rank, s.r AS sparse_rank,
       COALESCE(1.0 / (:k + d.r), 0) + COALESCE(1.0 / (:k + s.r), 0) AS rrf_score
FROM dense d FULL OUTER JOIN sparse s ON s.id = d.id
ORDER BY rrf_score DESC, chunk_id
LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class HybridHit:
    chunk_id: int
    rrf_score: float
    dense_rank: int | None
    sparse_rank: int | None


def keyword_query(question: str) -> str:
    """OR the question's words for websearch_to_tsquery.

    Its default is AND, which a natural-language question almost never satisfies. Postgres'
    English configuration drops stopwords, and ts_rank_cd rewards chunks matching more terms.
    """
    words = (word.lower() for word in _WORD.findall(question))
    return " or ".join(dict.fromkeys(w for w in words if w not in _OPERATOR_WORDS))


class HybridSearcher:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], rrf_k: int = 60) -> None:
        self._sessions = sessions
        self._rrf_k = rrf_k

    async def search(
        self,
        query_vec: Vectors,
        query_text: str,
        *,
        pmids: Sequence[int] | None = None,
        per_leg: int = 100,
        limit: int = 60,
    ) -> list[HybridHit]:
        """Top ``limit`` chunks by RRF; restricted to ``pmids`` when given."""
        if pmids is not None and not pmids:
            return []
        sql = _CANDIDATE_SQL if pmids is not None else _INDEX_SQL
        statement = text(sql).bindparams(bindparam("query_vec", type_=Vector(EMBEDDING_DIM)))
        params: dict[str, object] = {
            "query_vec": query_vec,
            "query_text": query_text,
            "title_section": TITLE_ONLY_SECTION,
            "per_leg": per_leg,
            "limit": limit,
            "k": self._rrf_k,
        }
        if pmids is not None:
            statement = statement.bindparams(bindparam("pmids", type_=ARRAY(BigInteger)))
            params["pmids"] = list(pmids)
        async with self._sessions() as session:
            rows = await session.execute(statement, params)
            return [
                HybridHit(
                    chunk_id=row.chunk_id,
                    rrf_score=float(row.rrf_score),
                    dense_rank=row.dense_rank,
                    sparse_rank=row.sparse_rank,
                )
                for row in rows
            ]


def merge_hits(*passes: Sequence[HybridHit]) -> list[HybridHit]:
    """Combine passes, keeping each chunk's best score, strongest first."""
    best: dict[int, HybridHit] = {}
    for hits in passes:
        for hit in hits:
            current = best.get(hit.chunk_id)
            if current is None or hit.rrf_score > current.rrf_score:
                best[hit.chunk_id] = hit
    return sorted(best.values(), key=lambda h: (-h.rrf_score, h.chunk_id))
