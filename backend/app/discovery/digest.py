"""Schemas for discovery digests: what the models return and what the app keeps."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.generation.answer import Citation
from app.ingest.metadata import Design


class ThemeGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clusters: list[str] = Field(default_factory=list)
    name: str = ""

    @field_validator("clusters", mode="before")
    @classmethod
    def _cluster_ids(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [f"K{v}" if isinstance(v, int) else str(v).strip().upper() for v in value]


class ThemeNames(BaseModel):
    """What the naming call returns (prompt themes@v1)."""

    model_config = ConfigDict(extra="ignore")

    themes: list[ThemeGroup] = Field(default_factory=list)


class SummaryDraft(BaseModel):
    """What a theme summary call returns (prompt digest@v1)."""

    model_config = ConfigDict(extra="ignore")

    summary: str


class ThemeArticle(BaseModel):
    pmid: int
    title: str
    design: Design
    badge: str
    year: int | None
    marker: str


class Theme(BaseModel):
    name: str
    summary: str
    evidence_mix: str  # computed from the articles' designs, e.g. "1 RCT, 3 Observational"
    articles: list[ThemeArticle]
    more_articles: int = 0  # members beyond the ones summarized and listed


class Digest(BaseModel):
    topic: str
    window_start: date | None
    window_end: date | None
    found: int  # PubMed matches in the window
    considered: int  # articles with abstracts that were grouped
    themes: list[Theme] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
