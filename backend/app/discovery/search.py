"""Find the articles a digest is built from.

The window is cut into roughly monthly slices and each slice contributes its share of
PubMed's best matches, so a six-month digest covers six months rather than the last few days.
Each slice is its own search, which also keeps every search within PubMed's 10,000-result
limit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from app.generation.plan import DateWindow
from app.generation.pubmed_query import date_filter
from app.sources.pubmed import SearchResult, Sort

MONTH_DAYS = 30
MAX_SLICES = 12
DATE_FIELD = "edat"


class Searcher(Protocol):
    async def esearch(
        self,
        term: str,
        *,
        retmax: int = 100,
        sort: Sort = "relevance",
        cache_ttl_s: int | None = None,
    ) -> SearchResult: ...


@dataclass(frozen=True, slots=True)
class WindowSlice:
    start: date
    end: date | None  # None: open-ended (the window runs to today)
    count: int
    taken: int


@dataclass(frozen=True, slots=True)
class DiscoveryHits:
    pmids: list[int]
    total: int  # matches in the whole window
    slices: list[WindowSlice]


def slice_window(start: date, end: date) -> list[tuple[date, date]]:
    """Contiguous, inclusive slices of about a month each, at most ``MAX_SLICES``."""
    days = (end - start).days + 1
    if days <= 0:
        return [(start, start)]
    count = max(1, min(MAX_SLICES, round(days / MONTH_DAYS)))
    return [
        (
            start + timedelta(days=days * i // count),
            start + timedelta(days=days * (i + 1) // count - 1),
        )
        for i in range(count)
    ]


def quotas(cap: int, slices: int) -> list[int]:
    """``cap`` shared across slices; the remainder goes to the most recent ones."""
    base, extra = divmod(cap, slices)
    return [base + (1 if i >= slices - extra else 0) for i in range(slices)]


async def find_articles(
    searcher: Searcher,
    query: str,
    window: DateWindow | None,
    *,
    cap: int,
    cache_ttl_s: int,
    today: date | None = None,
) -> DiscoveryHits:
    clause = date_filter(window.start, window.end, DATE_FIELD) if window else None
    if window is None or clause is None or clause not in query:
        result = await searcher.esearch(query, retmax=cap, cache_ttl_s=cache_ttl_s)
        return DiscoveryHits(pmids=list(result.pmids), total=result.count, slices=[])

    pieces = slice_window(window.start, window.end or today or date.today())
    shares = quotas(cap, len(pieces))
    bounds = [
        (start, None if i == len(pieces) - 1 and window.end is None else end)
        for i, (start, end) in enumerate(pieces)
    ]
    results = await asyncio.gather(
        *(
            searcher.esearch(
                query.replace(clause, date_filter(start, end, DATE_FIELD)),
                retmax=share,
                sort="relevance",
                cache_ttl_s=cache_ttl_s,
            )
            for (start, end), share in zip(bounds, shares, strict=True)
            if share > 0
        )
    )
    active = [b for b, share in zip(bounds, shares, strict=True) if share > 0]
    slices = [
        WindowSlice(start=start, end=end, count=r.count, taken=len(r.pmids))
        for (start, end), r in zip(active, results, strict=True)
    ]
    return DiscoveryHits(
        pmids=_round_robin([list(r.pmids) for r in reversed(results)])[:cap],
        total=sum(r.count for r in results),
        slices=slices,
    )


def _round_robin(lists: list[list[int]]) -> list[int]:
    """Interleave, newest slice first, without repeats; any cut keeps the slices balanced."""
    merged: list[int] = []
    seen: set[int] = set()
    for row in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if row < len(items) and items[row] not in seen:
                seen.add(items[row])
                merged.append(items[row])
    return merged
