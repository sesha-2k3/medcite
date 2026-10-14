"""Stream events (handoff section 7.9). Nodes emit them through LangGraph's custom stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langgraph.config import get_stream_writer

EventName = Literal[
    "step",
    "gap",
    "token",
    "citations",
    "evidence_table",
    "rating",
    "theme",
    "warning",
    "done",
    "error",
]
TOKEN_CHUNK_CHARS = 48


@dataclass(frozen=True, slots=True)
class Event:
    name: EventName
    data: Any


def emit(name: EventName, data: Any) -> None:
    get_stream_writer()(Event(name, data))


def emit_step(node: str, label: str, detail: str | None = None) -> None:
    data: dict[str, str] = {"node": node, "label": label}
    if detail:
        data["detail"] = detail
    emit("step", data)


def token_chunks(text: str, size: int = TOKEN_CHUNK_CHARS) -> list[str]:
    """Split at spaces into pieces of about ``size`` characters; joined, they equal ``text``."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            space = text.rfind(" ", start, end)
            end = space + 1 if space > start else end
        chunks.append(text[start:end])
        start = end
    return chunks
