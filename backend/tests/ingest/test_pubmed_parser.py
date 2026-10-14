from __future__ import annotations

from datetime import date

import pytest

from app.errors import UpstreamError
from app.ingest.pubmed_parser import UNTITLED, ParsedArticle, parse_pubmed_xml
from tests.ingest.helpers import edge_cases


@pytest.fixture(scope="module")
def articles() -> dict[int, ParsedArticle]:
    return edge_cases()


def test_parses_every_journal_article_and_skips_books(articles: dict[int, ParsedArticle]) -> None:
    assert sorted(articles) == list(range(90000101, 90000108))


def test_structured_abstract_keeps_labels_categories_and_inline_text(
    articles: dict[int, ParsedArticle],
) -> None:
    rct = articles[90000101]
    assert rct.is_structured
    categories = [s.category for s in rct.abstract]
    assert categories == ["BACKGROUND", "METHODS", "RESULTS", "CONCLUSIONS"]
    assert rct.abstract[2].text == "Muscle thickness rose 1.2 mm2 in both groups (p = 0.71)."
    assert rct.title.startswith("Dumbbell versus barbell curls for elbow flexor hypertrophy")


def test_full_record_metadata(articles: dict[int, ParsedArticle]) -> None:
    rct = articles[90000101]
    assert rct.journal == "Journal of Synthetic Strength Research"
    assert rct.language == "eng"
    assert rct.pub_date == date(2024, 3, 5)
    assert rct.entrez_date == date(2024, 2, 20)
    assert rct.pub_types == ("Journal Article", "Randomized Controlled Trial")
    assert rct.mesh_terms == ("Resistance Training", "Muscle, Skeletal")
    assert not rct.is_retracted


def test_identifiers_come_from_the_article_not_its_references(
    articles: dict[int, ParsedArticle],
) -> None:
    assert articles[90000101].doi == "10.1000/synthetic.101"
    assert articles[90000101].pmcid == "PMC9000101"


def test_doi_falls_back_to_elocation_id(articles: dict[int, ParsedArticle]) -> None:
    assert articles[90000102].doi == "10.1000/synthetic.102"
    assert articles[90000102].pmcid is None


def test_unstructured_abstract(articles: dict[int, ParsedArticle]) -> None:
    article = articles[90000102]
    assert article.has_abstract
    assert not article.is_structured
    assert article.journal == "Synth Sports Med"
    assert article.pub_date == date(2019, 11, 1)


def test_missing_abstract_and_free_form_date(articles: dict[int, ParsedArticle]) -> None:
    comment = articles[90000103]
    assert not comment.has_abstract
    assert comment.pub_date == date(2018, 12, 1)
    assert comment.entrez_date == date(2019, 1, 3)


def test_retraction_detected(articles: dict[int, ParsedArticle]) -> None:
    assert articles[90000104].is_retracted


def test_translated_title_loses_brackets_and_keeps_vernacular(
    articles: dict[int, ParsedArticle],
) -> None:
    spanish = articles[90000105]
    assert spanish.title == "Strength training in older adults."
    assert spanish.title_translated
    assert spanish.vernacular_title == "Entrenamiento de fuerza en adultos mayores."
    assert spanish.language == "spa"
    assert spanish.pub_date == date(2021, 3, 1)


def test_not_available_title_uses_vernacular_then_placeholder(
    articles: dict[int, ParsedArticle],
) -> None:
    assert articles[90000106].title == "Diabetes im Alter."
    assert articles[90000106].pub_date == date(2017, 1, 1)
    assert articles[90000107].title == UNTITLED


def test_unassigned_category_empty_sections_and_impossible_dates(
    articles: dict[int, ParsedArticle],
) -> None:
    article = articles[90000107]
    assert [(s.label, s.category) for s in article.abstract] == [
        ("Methods and results", None),
        ("Take-home message", None),
    ]
    assert article.pub_date == date(2015, 2, 1)
    assert article.journal is None
    assert article.entrez_date is None


def test_malformed_xml_raises_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        parse_pubmed_xml("<PubmedArticleSet><PubmedArticle>")


def test_empty_set_parses_to_nothing() -> None:
    assert parse_pubmed_xml("<PubmedArticleSet></PubmedArticleSet>") == []


def test_entities_are_not_expanded() -> None:
    hostile = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        "<PubmedArticleSet>&e;</PubmedArticleSet>"
    )
    assert parse_pubmed_xml(hostile) == []
