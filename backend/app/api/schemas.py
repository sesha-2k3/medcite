"""Request bodies for the /v1 API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ingest.metadata import StudyMetadata

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Text
    history: list[Text] = Field(
        default_factory=list, max_length=5, description="Earlier questions, oldest first"
    )


class DiscoverRequest(BaseModel):
    """A topic and a window: ``from``/``to`` dates, or the last ``months`` up to today."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    topic: Text
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")
    months: int = Field(default=6, ge=1, le=24)

    @model_validator(mode="after")
    def _check_window(self) -> DiscoverRequest:
        if self.start and self.end and self.start > self.end:
            raise ValueError("'from' must not be after 'to'")
        return self


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: int = Field(gt=0, description="From the done event of the answer being rated")
    value: Literal[1, -1]


class PassageOut(BaseModel):
    chunk_id: int
    section: str | None
    chunk_index: int | None
    content: str


class ArticleResponse(BaseModel):
    pmid: int
    title: str
    journal: str | None
    pub_date: date | None
    doi: str | None
    pub_types: list[str]
    publication_status: str | None
    is_retracted: bool
    design: str
    badge: str
    study_meta: StudyMetadata
    pubmed_url: str
    passages: list[PassageOut]
