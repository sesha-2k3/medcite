"""Fetch real PubMed records to check the client end to end.

    uv run python -m scripts.fetch_pubmed_sample "biceps curl hypertrophy" --count 20
    uv run python -m scripts.fetch_pubmed_sample "biceps curl hypertrophy" --record

``--record`` bypasses the cache and saves the raw responses under tests/fixtures/pubmed/recorded.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from lxml import etree

from app.config import get_settings
from app.db.redis import create_redis
from app.observability import configure_logging
from app.sources.factory import build_pubmed_client
from app.sources.http import create_http_client

RECORD_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pubmed" / "recorded"
SAFE_XML = etree.XMLParser(resolve_entities=False, no_network=True)


class RecordingCache:
    """Never hits; writes every response it is asked to store to ``directory``."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        _, endpoint, digest = key.split(":")
        name = endpoint.removesuffix(".fcgi")
        suffix = "json" if name == "esearch" else "xml"
        path = self._directory / f"{name}-{digest[:10]}.{suffix}"
        path.write_text(value, encoding="utf-8")
        print(f"recorded {path}")


def titles(xml_documents: list[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for document in xml_documents:
        root = etree.fromstring(document.encode(), SAFE_XML)
        for article in root.iterfind(".//PubmedArticle"):
            pmid = article.findtext(".//MedlineCitation/PMID", default="?")
            node = article.find(".//ArticleTitle")
            rows.append((pmid, "".join(node.itertext()) if node is not None else "(no title)"))
    return rows


async def run(query: str, count: int, record: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    redis = create_redis(settings)
    async with create_http_client(settings) as http:
        cache = RecordingCache(RECORD_DIR) if record else None
        client = build_pubmed_client(settings, http, redis, cache)
        result = await client.esearch(query, retmax=count)
        documents = await client.efetch(result.pmids)
    await redis.aclose()

    print(f"\nquery:       {query}")
    print(f"translation: {result.query_translation}")
    print(f"matches:     {result.count:,} (fetched {len(result.pmids)})")
    for warning in result.warnings:
        print(f"warning:     {warning}")
    print()
    for pmid, title in titles(documents):
        print(f"{pmid:>10}  {title[:100]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.fetch_pubmed_sample")
    parser.add_argument("query")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.count, args.record))


if __name__ == "__main__":
    main()
