"""A scripted PubMed searcher for planner and MeSH tests."""

from __future__ import annotations

from collections.abc import Callable

from app.sources.pubmed import SearchResult


class FakeSearcher:
    """``not_found`` answers MeSH checks; ``count`` gives each tier query's match count."""

    def __init__(
        self,
        not_found: list[str] | Exception,
        count: Callable[[str], int] = lambda _query: 10,
    ) -> None:
        self.not_found = not_found
        self.count = count
        self.terms: list[str] = []

    async def esearch(self, term: str, *, retmax: int = 100) -> SearchResult:
        self.terms.append(term)
        if isinstance(self.not_found, Exception):
            raise self.not_found
        is_mesh_check = "retracted publication" not in term
        return SearchResult(
            count=10 if is_mesh_check else self.count(term),
            pmids=[],
            query_translation=None,
            not_found=self.not_found if is_mesh_check else [],
        )

    @property
    def tier_searches(self) -> list[str]:
        return [t for t in self.terms if "retracted publication" in t]
