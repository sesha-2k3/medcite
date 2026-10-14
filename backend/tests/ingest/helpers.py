"""Shared builders for ingestion tests."""

from __future__ import annotations

from pathlib import Path

from app.ingest.pubmed_parser import ParsedArticle, parse_pubmed_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pubmed"
EDGE_CASES = FIXTURES / "efetch_edge_cases.xml"


def edge_cases() -> dict[int, ParsedArticle]:
    return {a.pmid: a for a in parse_pubmed_xml(EDGE_CASES.read_bytes())}
