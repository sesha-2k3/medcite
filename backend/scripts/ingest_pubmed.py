"""Search PubMed and ingest the results end to end, then show what was stored.

uv run python -m scripts.ingest_pubmed "biceps curl hypertrophy" --count 10 [--refresh]
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import Article, Chunk
from app.db.redis import create_redis
from app.db.session import create_engine, create_session_factory
from app.ingest.factory import build_ingest_service
from app.models.embeddings import load_embedder
from app.models.llm import UsageMeter, build_llm_client
from app.observability import configure_logging
from app.sources.factory import build_pubmed_client
from app.sources.http import create_http_client


async def run(query: str, count: int, refresh: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    print("loading MedCPT ...")
    embedder = await asyncio.to_thread(load_embedder, settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = create_redis(settings)
    meter = UsageMeter()
    try:
        async with create_http_client(settings) as http:
            pubmed = build_pubmed_client(settings, http, redis)
            service = build_ingest_service(
                settings, pubmed, build_llm_client(settings), embedder, sessions
            )
            result = await pubmed.esearch(query, retmax=count)
            report = await service.ingest(result.pmids, refresh=refresh, meter=meter)

        print(f"\nrequested {report.requested}, already stored {report.already_stored}")
        for outcome, n in sorted(report.outcomes.items()):
            print(f"  {outcome.value:<18} {n}")
        if report.not_returned:
            print(f"  not returned by PubMed: {report.not_returned}")
        tokens = sum(c.input_tokens + c.output_tokens for c in meter.calls)
        print(f"LLM calls {len(meter.calls)}, tokens {tokens:,}\n")
        await show(sessions, result.pmids)
    finally:
        await redis.aclose()
        await engine.dispose()


async def show(sessions: async_sessionmaker[AsyncSession], pmids: list[int]) -> None:
    async with sessions() as session:
        rows = await session.execute(
            select(
                Article.pmid,
                Article.title,
                Article.study_meta,
                func.array_agg(Chunk.section),
            )
            .join(Chunk, Chunk.article_id == Article.id, isouter=True)
            .where(Article.pmid.in_(pmids))
            .group_by(Article.id)
        )
        for pmid, title, meta, chunk_sections in rows:
            meta = meta or {}
            n = meta.get("sample_size")
            print(f"{pmid:>10}  {meta.get('design', '?'):<17} n={n!s:<5} {title[:60]}")
            print(f"{'':>12}chunks: {', '.join(s for s in chunk_sections if s)}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.ingest_pubmed")
    parser.add_argument("query")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.count, args.refresh))


if __name__ == "__main__":
    main()
