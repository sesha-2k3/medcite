"""Turn a parsed article into chunks: one per labeled abstract section, or one for the whole
abstract when it is unstructured.

Articles without an abstract get a single title-only chunk so they remain findable. Its section
is ``TITLE_ONLY_SECTION``, which retrieval must never cite as evidence for findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingest.pubmed_parser import AbstractSection, ParsedArticle

ABSTRACT_SECTION = "Abstract"
TITLE_ONLY_SECTION = "Title"


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    section: str
    chunk_index: int
    content: str
    embed_title: str
    embed_text: str


def chunk_article(article: ParsedArticle, study_type: str) -> list[ChunkDraft]:
    """Stored ``content`` is the plain text; only the embedding input carries context."""
    if not article.has_abstract:
        return [_draft(article, TITLE_ONLY_SECTION, 0, article.title, study_type)]
    if not article.is_structured:
        text = " ".join(section.text for section in article.abstract)
        return [_draft(article, ABSTRACT_SECTION, 0, text, study_type)]
    return [
        _draft(article, section_name(section), index, section.text, study_type)
        for index, section in enumerate(article.abstract)
    ]


def section_name(section: AbstractSection) -> str:
    """``Abstract-RESULTS``: NLM's normalized category when present, else the author's label."""
    name = section.category or section.label
    if not name:
        return ABSTRACT_SECTION
    return f"{ABSTRACT_SECTION}-{'_'.join(name.upper().split())}"


def _draft(
    article: ParsedArticle, section: str, index: int, content: str, study_type: str
) -> ChunkDraft:
    return ChunkDraft(
        section=section,
        chunk_index=index,
        content=content,
        embed_title=article.title,
        embed_text=f"{section} | {study_type} | {content}",
    )
