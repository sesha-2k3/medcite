from __future__ import annotations

import pytest

from app.ingest.chunker import ABSTRACT_SECTION, TITLE_ONLY_SECTION, chunk_article, section_name
from app.ingest.pubmed_parser import AbstractSection, ParsedArticle
from tests.ingest.helpers import edge_cases


@pytest.fixture(scope="module")
def articles() -> dict[int, ParsedArticle]:
    return edge_cases()


def test_structured_abstract_gives_one_chunk_per_section(
    articles: dict[int, ParsedArticle],
) -> None:
    chunks = chunk_article(articles[90000101], "rct")
    assert [c.section for c in chunks] == [
        "Abstract-BACKGROUND",
        "Abstract-METHODS",
        "Abstract-RESULTS",
        "Abstract-CONCLUSIONS",
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]


def test_stored_text_is_plain_and_embedding_input_carries_context(
    articles: dict[int, ParsedArticle],
) -> None:
    article = articles[90000101]
    results = chunk_article(article, "rct")[2]
    assert results.content.startswith("Muscle thickness rose")
    assert results.embed_title == article.title
    assert results.embed_text == f"Abstract-RESULTS | rct | {results.content}"


def test_unstructured_abstract_is_one_chunk(articles: dict[int, ParsedArticle]) -> None:
    [chunk] = chunk_article(articles[90000102], "other")
    assert chunk.section == ABSTRACT_SECTION
    assert chunk.content.startswith("Longer rest between sets")


def test_missing_abstract_gives_a_title_only_chunk(articles: dict[int, ParsedArticle]) -> None:
    [chunk] = chunk_article(articles[90000103], "commentary")
    assert chunk.section == TITLE_ONLY_SECTION
    assert chunk.content == "Response."


@pytest.mark.parametrize(
    ("label", "category", "expected"),
    [
        ("AIMS", "OBJECTIVE", "Abstract-OBJECTIVE"),
        ("Methods and results", None, "Abstract-METHODS_AND_RESULTS"),
        (None, None, "Abstract"),
    ],
)
def test_section_names_prefer_nlm_category(
    label: str | None, category: str | None, expected: str
) -> None:
    assert section_name(AbstractSection(label=label, category=category, text="x")) == expected
