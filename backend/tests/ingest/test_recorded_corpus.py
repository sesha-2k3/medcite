"""Invariants over every minimized real PubMed recording (tests/fixtures/pubmed/minimized).

Record and retraction counts come from the raw markup, independently of the parser; the chunk
checks confirm the chunker agrees with what the parser found.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.chunker import TITLE_ONLY_SECTION, chunk_article
from app.ingest.pubmed_parser import parse_pubmed_xml
from tests.ingest.helpers import FIXTURES

CORPUS = sorted((FIXTURES / "minimized").glob("efetch_*.xml"))


@pytest.mark.skipif(not CORPUS, reason="no minimized fixtures; run make fixtures-minimize")
@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_recorded_records_parse_and_chunk_consistently(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    articles = parse_pubmed_xml(raw)

    assert len(articles) == raw.count("<PubmedArticle>")
    assert sum(a.is_retracted for a in articles) == raw.count(
        ">Retracted Publication</PublicationType>"
    )
    for article in articles:
        assert article.pmid > 0
        assert article.title
        assert not article.title.startswith("[")

    chunks = [c for a in articles if not a.is_retracted for c in chunk_article(a, "other")]
    live = [a for a in articles if not a.is_retracted]
    labeled = sum(1 for a in live for s in a.abstract if s.label or s.category)
    assert sum(c.section.startswith("Abstract-") for c in chunks) == labeled
    assert sum(c.section == TITLE_ONLY_SECTION for c in chunks) == sum(
        not a.has_abstract for a in live
    )
