"""Parse PubMed EFetch XML into plain, immutable article records.

Robust to missing fields: anything absent becomes ``None`` or empty rather than an error.
Book records (``PubmedBookArticle``) and deletion notices are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from lxml import etree

from app.errors import UpstreamError

RETRACTED_PUBLICATION = "Retracted Publication"
UNTITLED = "Untitled"
_NOT_AVAILABLE = {"[not available].", "[not available]", "not available"}
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}
_YEAR = re.compile(r"\b(1[89]\d\d|2\d\d\d)\b")


@dataclass(frozen=True, slots=True)
class AbstractSection:
    label: str | None
    category: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ParsedArticle:
    pmid: int
    title: str
    title_translated: bool
    vernacular_title: str | None
    journal: str | None
    language: str | None
    pub_date: date | None
    entrez_date: date | None
    doi: str | None
    pmcid: str | None
    pub_types: tuple[str, ...]
    mesh_terms: tuple[str, ...]
    abstract: tuple[AbstractSection, ...]
    is_retracted: bool

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract)

    @property
    def is_structured(self) -> bool:
        return any(section.label or section.category for section in self.abstract)


def parse_pubmed_xml(document: str | bytes) -> list[ParsedArticle]:
    """Parse one EFetch response. Raises UpstreamError if it isn't well-formed XML."""
    raw = document.encode() if isinstance(document, str) else document
    try:
        root = etree.fromstring(raw, _SAFE_PARSER)
    except etree.XMLSyntaxError as exc:
        raise UpstreamError(f"Unparseable PubMed XML: {exc}") from exc
    parsed = (_parse_article(node) for node in root.iterfind("PubmedArticle"))
    return [article for article in parsed if article is not None]


def _parse_article(node: Any) -> ParsedArticle | None:
    citation = node.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    pmid = _text(citation.find("PMID")) if citation is not None else None
    if article is None or not pmid or not pmid.isdigit():
        return None
    vernacular = _text(article.find("VernacularTitle"))
    title, translated = _clean_title(_text(article.find("ArticleTitle")), vernacular)
    pub_types = tuple(_texts(article.iterfind("PublicationTypeList/PublicationType")))
    return ParsedArticle(
        pmid=int(pmid),
        title=title,
        title_translated=translated,
        vernacular_title=vernacular,
        journal=_text(article.find("Journal/Title"))
        or _text(article.find("Journal/ISOAbbreviation")),
        language=_text(article.find("Language")),
        pub_date=_pub_date(article),
        entrez_date=_history_date(node, "entrez") or _history_date(node, "pubmed"),
        doi=_article_id(node, "doi") or _elocation_doi(article),
        pmcid=_article_id(node, "pmc"),
        pub_types=pub_types,
        mesh_terms=tuple(_texts(citation.iterfind("MeshHeadingList/MeshHeading/DescriptorName"))),
        abstract=_abstract(article),
        is_retracted=RETRACTED_PUBLICATION in pub_types or _has_retraction_link(citation),
    )


def _text(node: Any) -> str | None:
    """All text inside an element, including inline markup like <i> or <sup>."""
    if node is None:
        return None
    value = " ".join("".join(node.itertext()).split())
    return value or None


def _texts(nodes: Any) -> list[str]:
    return [value for node in nodes if (value := _text(node))]


def _clean_title(title: str | None, vernacular: str | None) -> tuple[str, bool]:
    """Resolve PubMed's placeholders; strip the brackets that mark translated titles."""
    if title is None or title.lower() in _NOT_AVAILABLE:
        return vernacular or UNTITLED, False
    if title.startswith("["):
        closing = title.rfind("]")
        if closing > 0:
            return (title[1:closing] + title[closing + 1 :]).strip(), True
    return title, False


def _abstract(article: Any) -> tuple[AbstractSection, ...]:
    sections: list[AbstractSection] = []
    for node in article.iterfind("Abstract/AbstractText"):
        text = _text(node)
        if text:
            category = node.get("NlmCategory")
            sections.append(
                AbstractSection(
                    label=node.get("Label"),
                    category=None if category == "UNASSIGNED" else category,
                    text=text,
                )
            )
    return tuple(sections)


def _article_id(node: Any, id_type: str) -> str | None:
    # Direct child path only: references deeper in the record carry their own ArticleIdLists.
    for id_node in node.iterfind("PubmedData/ArticleIdList/ArticleId"):
        if id_node.get("IdType") == id_type:
            return _text(id_node)
    return None


def _elocation_doi(article: Any) -> str | None:
    for node in article.iterfind("ELocationID"):
        if node.get("EIdType") == "doi":
            return _text(node)
    return None


def _has_retraction_link(citation: Any) -> bool:
    return any(
        node.get("RefType") == "RetractionIn"
        for node in citation.iterfind("CommentsCorrectionsList/CommentsCorrections")
    )


def _pub_date(article: Any) -> date | None:
    node = article.find("Journal/JournalIssue/PubDate")
    if node is not None:
        parsed = _date_from_parts(node) or _date_from_medline(_text(node.find("MedlineDate")))
        if parsed:
            return parsed
    electronic = article.find("ArticleDate")
    return _date_from_parts(electronic) if electronic is not None else None


def _history_date(node: Any, status: str) -> date | None:
    for entry in node.iterfind("PubmedData/History/PubMedPubDate"):
        if entry.get("PubStatus") == status:
            return _date_from_parts(entry)
    return None


def _date_from_parts(node: Any) -> date | None:
    """Year plus optional Month (number, name or season) and Day; missing parts become 1."""
    year = _text(node.find("Year"))
    if not year or not year.isdigit():
        return None
    month = _month(_text(node.find("Month")) or _text(node.find("Season")))
    day_text = _text(node.find("Day"))
    day = int(day_text) if day_text and day_text.isdigit() else 1
    return _safe_date(int(year), month or 1, day)


def _date_from_medline(value: str | None) -> date | None:
    """Free-form dates such as '1998 Dec-1999 Jan' or '2000 Spring': first year and month."""
    if not value:
        return None
    match = _YEAR.search(value)
    if not match:
        return None
    rest = value[match.end() :].split()
    month = _month(rest[0].split("-")[0]) if rest else None
    return _safe_date(int(match.group(1)), month or 1, 1)


def _month(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        number = int(value)
        return number if 1 <= number <= 12 else None
    key = value.lower()
    return _MONTHS.get(key[:3]) or _SEASONS.get(key)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        try:
            return date(year, month, 1)
        except ValueError:
            return None
