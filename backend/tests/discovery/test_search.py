from __future__ import annotations

import itertools
from datetime import date

from app.discovery.search import find_articles, quotas, slice_window
from app.generation.plan import DateWindow
from app.generation.pubmed_query import date_filter
from tests.graph.fakes import FakeSearcher

START, TODAY = date(2026, 4, 14), date(2026, 10, 14)
TOPIC = '("Diabetes Mellitus"[mh] OR diabetes[tiab])'


def query(start: date, end: date | None) -> str:
    return f"{TOPIC} AND {date_filter(start, end, 'edat')}"


def test_six_months_become_six_contiguous_slices() -> None:
    slices = slice_window(START, TODAY)
    assert len(slices) == 6
    assert slices[0][0] == START
    assert slices[-1][1] == TODAY
    for (_, end), (start, _) in itertools.pairwise(slices):
        assert (start - end).days == 1


def test_short_and_long_windows() -> None:
    assert len(slice_window(date(2026, 10, 1), TODAY)) == 1
    assert len(slice_window(date(2020, 1, 1), TODAY)) == 12


def test_quotas_share_the_cap_and_favour_recent_slices() -> None:
    assert quotas(50, 6) == [8, 8, 8, 8, 9, 9]
    assert sum(quotas(7, 3)) == 7


async def test_relevance_search_takes_the_best_matches_from_every_month() -> None:
    slices = slice_window(START, TODAY)
    bounds = [(s, None if i == len(slices) - 1 else e) for i, (s, e) in enumerate(slices)]
    pmids = {
        query(s, e): [1000 * (i + 1) + k for k in range(20)] for i, (s, e) in enumerate(bounds)
    }
    counts = {q: 900 + i for i, q in enumerate(pmids)}
    searcher = FakeSearcher(pmids, counts)

    hits = await find_articles(
        searcher,
        query(START, None),
        DateWindow(start=START),
        cap=12,
        cache_ttl_s=3600,
        today=TODAY,
    )
    assert len(searcher.calls) == 6
    assert {call["sort"] for call in searcher.calls} == {"relevance"}
    assert {call["ttl"] for call in searcher.calls} == {3600}
    assert [call["retmax"] for call in searcher.calls] == quotas(12, 6)
    assert hits.total == sum(counts.values())
    assert len(hits.pmids) == 12
    assert {p // 1000 for p in hits.pmids} == {1, 2, 3, 4, 5, 6}  # every month represented
    assert hits.pmids[0] == 6000  # newest slice first
    assert searcher.calls[-1]["term"].endswith(":3000[edat]")  # the window stays open


async def test_without_a_window_one_search() -> None:
    searcher = FakeSearcher({TOPIC: [1, 2, 3]})
    hits = await find_articles(searcher, TOPIC, None, cap=50, cache_ttl_s=60, today=TODAY)
    assert hits.pmids == [1, 2, 3]
    assert len(searcher.calls) == 1


async def test_repeats_across_slices_are_dropped() -> None:
    slices = slice_window(START, date(2026, 6, 13))
    bounds = [(s, e) for s, e in slices]
    pmids = {query(s, e): [7, 8] for s, e in bounds}
    hits = await find_articles(
        FakeSearcher(pmids),
        query(START, date(2026, 6, 13)),
        DateWindow(start=START, end=date(2026, 6, 13)),
        cap=10,
        cache_ttl_s=60,
    )
    assert hits.pmids == [7, 8]
