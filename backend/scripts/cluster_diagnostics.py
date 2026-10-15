"""Pairwise cosine distances of stored MedCPT vectors for a discovery run, raw and centered.

Reads a discovery row from query_logs and loads its articles' vectors exactly as clustering
does (DiscoveryArticles.load). Needs only Postgres: no MedCPT, LLM or network.

uv run python -m scripts.cluster_diagnostics            # latest discovery run
uv run python -m scripts.cluster_diagnostics --list     # recent discovery runs
uv run python -m scripts.cluster_diagnostics --query-id 42
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
from sklearn.cluster import AgglomerativeClustering
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import QueryLog
from app.db.queries.discovery import DiscoveryArticle, DiscoveryArticles
from app.db.session import create_engine, create_session_factory
from app.discovery.clusters import cluster_vectors
from app.observability import configure_logging

Vectors = npt.NDArray[np.float32]
PERCENTILES = (5, 10, 25, 50, 75, 90)
SWEEP = (0.35, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1)


def center(vectors: Vectors) -> Vectors:
    """Subtract the batch mean, then rescale rows to unit length."""
    shifted = vectors - vectors.mean(axis=0)
    norms = np.linalg.norm(shifted, axis=1, keepdims=True)
    return (shifted / np.where(norms == 0, 1.0, norms)).astype(np.float32)


def distance_matrix(vectors: Vectors) -> npt.NDArray[np.float64]:
    """Cosine distances; rows are assumed unit length."""
    similarity = np.clip(vectors.astype(np.float64) @ vectors.T.astype(np.float64), -1.0, 1.0)
    return 1.0 - similarity


def pair_distances(matrix: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    return matrix[np.triu_indices(len(matrix), k=1)]


def nearest_neighbour(matrix: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    masked = matrix + np.diag(np.full(len(matrix), np.inf))
    return masked.min(axis=1)


def target_groups(count: int, per_theme: int, max_themes: int) -> int:
    """About one group per ``per_theme`` articles, at least 2, at most ``max_themes``."""
    return max(2, min(max_themes, round(count / per_theme)))


def cut_into(vectors: Vectors, groups: int) -> list[list[int]]:
    """Average-linkage cosine tree cut into ``groups`` clusters, largest first."""
    labels = AgglomerativeClustering(
        n_clusters=groups, metric="cosine", linkage="average"
    ).fit_predict(vectors)
    members: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        members.setdefault(int(label), []).append(index)
    return sorted(members.values(), key=lambda m: (-len(m), min(m)))


def stats_row(label: str, values: npt.NDArray[np.float64], threshold: float) -> str:
    cells = [f"{values.min():5.2f}"]
    cells += [f"{np.percentile(values, p):5.2f}" for p in PERCENTILES]
    cells += [f"{values.max():5.2f}", f"{(values < threshold).mean():6.0%}"]
    return f"{label:<22}" + " ".join(cells)


def histogram(raw: npt.NDArray[np.float64], centered: npt.NDArray[np.float64]) -> list[str]:
    edges = np.round(np.arange(0.0, 2.01, 0.1), 1)
    raw_counts, _ = np.histogram(raw, bins=edges)
    centered_counts, _ = np.histogram(centered, bins=edges)
    scale = max(int(raw_counts.max()), int(centered_counts.max()), 1)
    lines = []
    for low, a, b in zip(edges[:-1], raw_counts, centered_counts, strict=True):
        if a or b:
            bar_a, bar_b = "#" * round(24 * a / scale), "#" * round(24 * b / scale)
            span = f"{low:3.1f}-{low + 0.1:3.1f}"
            lines.append(f"  {span}  {bar_a:<24} {a:>5}   {bar_b:<24} {b:>5}")
    return lines


def sizes(clusters: Sequence[Sequence[int]]) -> str:
    return "[" + ", ".join(str(len(c)) for c in clusters) + "]"


async def recent_runs(sessions: async_sessionmaker[AsyncSession], limit: int) -> list[QueryLog]:
    query = (
        select(QueryLog)
        .where(QueryLog.mode == "discovery")
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
    )
    async with sessions() as session:
        return list((await session.execute(query)).scalars())


async def one_run(sessions: async_sessionmaker[AsyncSession], query_id: int) -> QueryLog | None:
    async with sessions() as session:
        return await session.get(QueryLog, query_id)


def report(
    run: QueryLog,
    articles: list[DiscoveryArticle],
    threshold: float,
    per_theme: int,
    max_themes: int,
) -> None:
    pmids = run.candidate_pmids or []
    raw = np.stack([a.vector for a in articles])
    centered = center(raw)
    raw_m, cen_m = distance_matrix(raw), distance_matrix(centered)
    raw_d, cen_d = pair_distances(raw_m), pair_distances(cen_m)

    print(f'run {run.id}  {run.created_at:%Y-%m-%d %H:%M}  "{run.user_query}"')
    print(f"{len(articles)} of {len(pmids)} PMIDs have abstract vectors; {len(raw_d)} pairs")
    shared = float(np.linalg.norm(raw.mean(axis=0)))
    print(f"norm of the mean vector: {shared:.3f} (0 = no shared direction, 1 = all identical)\n")

    header = " ".join(f"{h:>5}" for h in ["min", *(f"p{p}" for p in PERCENTILES), "max"])
    print(f"{'cosine distance':<22}{header} {'<' + str(threshold):>6}")
    print(stats_row("all pairs, raw", raw_d, threshold))
    print(stats_row("all pairs, centered", cen_d, threshold))
    print(stats_row("nearest nbr, raw", nearest_neighbour(raw_m), threshold))
    print(stats_row("nearest nbr, centered", nearest_neighbour(cen_m), threshold))

    print(f"\n  {'bin':<9}  {'raw':<30}   centered")
    print("\n".join(histogram(raw_d, cen_d)))

    print("\nthreshold cut (current method, groups of >= 2, uncapped)")
    print(f"  {'cut':>4}  {'raw sizes':<36} centered sizes")
    for cut in SWEEP:
        a = cluster_vectors(raw, distance=cut, max_clusters=len(raw))
        b = cluster_vectors(centered, distance=cut, max_clusters=len(raw))
        print(f"  {cut:4.2f}  {sizes(a):<36} {sizes(b)}")

    groups = target_groups(len(articles), per_theme, max_themes)
    raw_k, cen_k = cut_into(raw, groups), cut_into(centered, groups)
    rule = f"about one per {per_theme} articles, max {max_themes}"
    print(f"\ntarget cut into {groups} groups ({rule})")
    print(f"  raw      {sizes(raw_k)}")
    print(f"  centered {sizes(cen_k)}")
    for label, clusters in (("raw", raw_k), ("centered", cen_k)):
        print(f"\n{label}: first titles per group")
        for number, members in enumerate(clusters, start=1):
            print(f"  group {number} ({len(members)})")
            for index in members[:4]:
                passage = articles[index].passage
                print(f"    {passage.pmid:>9}  {passage.title[:86]}")


async def run(query_id: int | None, list_only: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    engine = create_engine(settings)
    try:
        sessions = create_session_factory(engine)
        if list_only:
            for row in await recent_runs(sessions, 10):
                count = len(row.candidate_pmids or [])
                when = f"{row.created_at:%Y-%m-%d %H:%M}"
                print(f"{row.id:>6}  {when}  {count:>4} PMIDs  {row.user_query}")
            return
        if query_id is None:
            latest = await recent_runs(sessions, 1)
            found = latest[0] if latest else None
        else:
            found = await one_run(sessions, query_id)
        if found is None or found.mode != "discovery":
            print("no such discovery run in query_logs (try --list)")
            return
        articles = await DiscoveryArticles(sessions).load(found.candidate_pmids or [])
        if len(articles) < 3:
            print(f"run {found.id}: only {len(articles)} articles have vectors; nothing to compare")
            return
        report(
            found,
            articles,
            settings.discovery_cluster_distance,
            settings.discovery_articles_per_theme,
            settings.discovery_max_themes,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.cluster_diagnostics")
    parser.add_argument("--query-id", type=int, help="query_logs.id of a discovery run")
    parser.add_argument("--list", action="store_true", help="list recent discovery runs")
    args = parser.parse_args()
    asyncio.run(run(args.query_id, args.list))


if __name__ == "__main__":
    main()
