"""In-memory stand-ins for the rate limiter and cache."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pubmed"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def efetch_xml(pmids: list[str]) -> str:
    articles = "".join(
        f'<PubmedArticle><MedlineCitation><PMID Version="1">{p}</PMID></MedlineCitation>'
        "</PubmedArticle>"
        for p in pmids
    )
    return f"<?xml version='1.0'?><PubmedArticleSet>{articles}</PubmedArticleSet>"


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1


class MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self.store[key] = value
        self.ttls[key] = ttl_s
