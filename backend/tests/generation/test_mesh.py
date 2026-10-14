from __future__ import annotations

import pytest

from app.errors import UpstreamError
from app.generation.mesh import MeshChecker
from tests.generation.fakes import FakeSearcher


async def test_one_search_identifies_unknown_headings() -> None:
    searcher = FakeSearcher(['"Biceps Brachii"[mh]', '"Muscle Hypertrophy"[mh]'])
    unknown = await MeshChecker(searcher).unknown(
        ["Resistance Training", "Biceps Brachii", "Muscle Hypertrophy", "Biceps Brachii"]
    )
    assert unknown == {"Biceps Brachii", "Muscle Hypertrophy"}
    assert searcher.terms == [
        '"Resistance Training"[mh] OR "Biceps Brachii"[mh] OR "Muscle Hypertrophy"[mh]'
    ]


async def test_matching_ignores_case_and_spacing() -> None:
    searcher = FakeSearcher(['"biceps brachii"[MeSH Terms]'])
    assert await MeshChecker(searcher).unknown(["Biceps  Brachii"]) == {"Biceps  Brachii"}


async def test_all_known() -> None:
    assert await MeshChecker(FakeSearcher([])).unknown(["Hypertrophy"]) == set()


async def test_no_headings_sends_no_request() -> None:
    searcher = FakeSearcher([])
    assert await MeshChecker(searcher).unknown([]) == set()
    assert searcher.terms == []


async def test_search_errors_propagate_for_the_caller_to_handle() -> None:
    with pytest.raises(UpstreamError):
        await MeshChecker(FakeSearcher(UpstreamError("down"))).unknown(["Hypertrophy"])
