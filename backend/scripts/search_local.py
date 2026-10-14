"""Rank stored passages for a question, using only the local index (no PubMed, no LLM).

uv run python -m scripts.search_local "dumbbell vs barbell curls for biceps growth"
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.models.cross_encoder import load_cross_encoder
from app.models.embeddings import load_embedder
from app.observability import configure_logging
from app.retrieval.evidence import CitedEvidence, suggest_rating
from app.retrieval.factory import build_retriever


async def run(question: str) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    print("loading MedCPT ...")
    embedder = await asyncio.to_thread(load_embedder, settings)
    scorer = await asyncio.to_thread(load_cross_encoder, settings)
    engine = create_engine(settings)
    try:
        retriever = build_retriever(settings, create_session_factory(engine), embedder, scorer)
        passages = await retriever.retrieve(question)
    finally:
        await engine.dispose()

    print(f"\n{question}\n")
    print(f"{'#':>2} {'score':>6} {'dense':>5} {'sparse':>6}  {'pmid':>9}  section / evidence")
    for rank, passage in enumerate(passages, start=1):
        s, g = passage.scored, passage.grade
        dense = s.hit.dense_rank if s.hit.dense_rank is not None else "-"
        sparse = s.hit.sparse_rank if s.hit.sparse_rank is not None else "-"
        flags = f" [{', '.join(g.modifiers)}]" if g.modifiers else ""
        print(
            f"{rank:>2} {s.relevance:>6.2f} {dense!s:>5} {sparse!s:>6}  {s.chunk.pmid:>9}  "
            f"{s.chunk.section} / {g.badge}{flags}"
        )
        print(f"{'':>34}{s.chunk.title[:70]}")
    rating = suggest_rating([CitedEvidence(p.grade, p.tier) for p in passages])
    print(f"\nsuggested rating: {rating.rating} ({'; '.join(rating.reasons)})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.search_local")
    parser.add_argument("question")
    asyncio.run(run(parser.parse_args().question))


if __name__ == "__main__":
    main()
