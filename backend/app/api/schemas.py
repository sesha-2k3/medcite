"""Request bodies for the /v1 API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Text
    history: list[Text] = Field(
        default_factory=list, max_length=5, description="Earlier questions, oldest first"
    )
