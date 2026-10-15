"""Wiring for answer generation: the context budget from settings."""

from __future__ import annotations

from app.config import Settings
from app.generation.context import ContextBudget


def context_budget(settings: Settings) -> ContextBudget:
    return ContextBudget(
        max_tokens=settings.generation_context_tokens,
        passage_max_chars=settings.passage_max_chars,
        min_chunks=settings.context_min_chunks,
        max_chunks=settings.context_max_chunks,
    )
