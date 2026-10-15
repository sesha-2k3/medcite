from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.generation.context import ContextBudget
from app.generation.factory import context_budget


def test_budget_comes_from_settings(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(
        generation_context_tokens=900,
        passage_max_chars=500,
        context_min_chunks=2,
        context_max_chunks=5,
    )
    assert context_budget(settings) == ContextBudget(
        max_tokens=900, passage_max_chars=500, min_chunks=2, max_chunks=5
    )
