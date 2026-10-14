from __future__ import annotations

import pytest

from app.generation.context import (
    ContextBudget,
    article_header,
    build_context,
    estimate_tokens,
    rating_ceiling,
    trim_text,
)
from app.ingest.chunker import TITLE_ONLY_SECTION
from app.retrieval.retriever import EvidencePassage
from tests.generation.builders import creatine_passages, passage


def test_markers_follow_article_groups_in_rank_order() -> None:
    context = build_context(creatine_passages())
    assert [(i.marker, i.chunk.chunk_id) for i in context.items] == [
        ("C1", 11),
        ("C2", 12),
        ("C3", 21),
        ("C4", 31),
    ]
    assert context.by_marker["C3"].chunk.pmid == 90_000_002
    assert context.omitted == 0


def test_each_article_header_and_title_appear_once() -> None:
    text = build_context(creatine_passages()).text
    assert text.count("## PMID 90000001") == 1
    assert text.count("Title: Synthetic study 1") == 1
    assert "[C2] CONCLUSIONS: Creatine supplementation" in text


def test_header_is_compact_and_omits_unknowns() -> None:
    trial, cohort, _, review = creatine_passages()
    assert article_header(trial) == (
        "## PMID 90000001 | RCT | n=40 | 2024 | direct | flags: small sample"
    )
    assert article_header(cohort) == "## PMID 90000002 | cohort | n=310 | 2023 | direct"
    assert article_header(review) == "## PMID 90000003 | systematic review | 2021 | indirect"


def test_header_shows_outcome_class() -> None:
    item = passage(51, 5, "x", outcome="muscle thickness", outcome_class="physiological", tier=None)
    assert "outcome: muscle thickness (physiological)" in article_header(item)
    assert "direct" not in article_header(item)


def test_title_only_chunks_are_rejected() -> None:
    with pytest.raises(ValueError, match="title-only"):
        build_context([passage(61, 6, "A title", section=TITLE_ONLY_SECTION)])


def test_trim_cuts_at_a_sentence_end() -> None:
    text = "First sentence is here. Second sentence is longer than the limit allows."
    assert trim_text(text, 40) == "First sentence is here. \u2026"
    assert trim_text("short", 40) == "short"


def long_passages(count: int) -> list[EvidencePassage]:
    sentence = "Muscle thickness increased in both groups after training. "
    return [
        passage(100 + i, 100 + i, sentence * 50, relevance=10.0 - i * 0.1) for i in range(count)
    ]


def test_budget_keeps_the_minimum_by_trimming_and_drops_lowest_ranked() -> None:
    budget = ContextBudget(max_tokens=3400, passage_max_chars=1400, min_chunks=8, max_chunks=15)
    context = build_context(long_passages(20), budget)
    assert context.estimated_tokens <= budget.max_tokens
    assert estimate_tokens(context.text) == context.estimated_tokens
    assert 8 <= len(context.items) <= 15
    assert [i.chunk.chunk_id for i in context.items] == list(range(100, 100 + len(context.items)))
    assert context.omitted == 20 - len(context.items)


def test_hard_budget_wins_over_the_minimum() -> None:
    budget = ContextBudget(max_tokens=300, passage_max_chars=1400, min_chunks=8, max_chunks=15)
    context = build_context(long_passages(10), budget)
    assert context.estimated_tokens <= 300
    assert 1 <= len(context.items) < 8


def test_empty_context() -> None:
    context = build_context([])
    assert context.items == ()
    assert context.text == ""


def test_rating_ceiling_counts_each_article_once() -> None:
    items = build_context(creatine_passages()).items
    ceiling = rating_ceiling(items)
    assert ceiling.rating == "low"
    assert "small samples" in ceiling.reasons
