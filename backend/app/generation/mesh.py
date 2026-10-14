"""Check that MeSH headings proposed by the planner model exist.

One ESearch ORs every heading as ``"X"[mh]``; PubMed lists each quoted phrase it does not
recognize, which identifies invented headings without a request per heading.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.generation.pubmed_query import clean_term
from app.sources.pubmed import SearchResult

_FIELD_TAG = re.compile(r"\[[^\]]*\]\s*$")


class TermSearcher(Protocol):
    async def esearch(self, term: str, *, retmax: int = 100) -> SearchResult: ...


class MeshChecker:
    def __init__(self, searcher: TermSearcher) -> None:
        self._searcher = searcher

    async def unknown(self, headings: Sequence[str]) -> set[str]:
        """The headings, as given, that PubMed does not recognize as MeSH."""
        candidates = {cleaned: h for h in headings if (cleaned := clean_term(h))}
        if not candidates:
            return set()
        query = " OR ".join(f'"{heading}"[mh]' for heading in candidates)
        result = await self._searcher.esearch(query, retmax=0)
        missing = {_heading(item) for item in result.not_found}
        return {original for cleaned, original in candidates.items() if cleaned.lower() in missing}


def _heading(item: str) -> str:
    """'"Biceps Brachii"[mh]' becomes 'biceps brachii'."""
    return _FIELD_TAG.sub("", item).strip().strip('"').lower()
