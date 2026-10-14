from __future__ import annotations

from decimal import Decimal

import pytest

from app.generation.citations import (
    checkable_numbers,
    marker_ids,
    normalize_marker,
    normalize_text,
    numbers_in,
    sentences,
    strip_markers,
    text_units,
)


@pytest.mark.parametrize(
    ("text", "markers"),
    [
        ("Gains were larger [C1].", ["C1"]),
        ("Gains were larger [C1, C3].", ["C1", "C3"]),
        ("Gains were larger [C1][C2] and [c3; C1].", ["C1", "C2", "C3"]),
        ("Ranges are not markers [C1-C3].", []),
        ("No markers here.", []),
    ],
)
def test_marker_ids(text: str, markers: list[str]) -> None:
    assert marker_ids(text) == markers


def test_normalize_marker() -> None:
    assert normalize_marker(" [c12] ") == "C12"


def test_marker_after_full_stop_stays_with_its_sentence() -> None:
    found = sentences("Strength rose. [C1] It fell later.")
    assert found == ["Strength rose [C1].", "It fell later."]


def test_abbreviations_and_decimals_do_not_split() -> None:
    text = "Dumbbell vs. barbell e.g. curls gave 1.5 kg [C1]. Smith et al. Found more [C2]."
    assert sentences(text) == [
        "Dumbbell vs. barbell e.g. curls gave 1.5 kg [C1].",
        "Smith et al. Found more [C2].",
    ]


def test_headings_lead_ins_and_list_items() -> None:
    units = text_units(
        "### Findings\n**Key points**\nBottom line:\n- First item [C1].\n1. Second item [C2]."
    )
    assert [(u.kind, u.text) for u in units] == [
        ("heading", "Findings"),
        ("heading", "Key points"),
        ("lead_in", "Bottom line:"),
        ("sentence", "First item [C1]."),
        ("sentence", "Second item [C2]."),
    ]


def test_unpunctuated_list_items_are_still_sentences() -> None:
    assert text_units("- Better adherence with dumbbells")[0].kind == "sentence"


def test_numbers_are_compared_by_value() -> None:
    assert numbers_in("1,200 people, 2.10 kg, 1\u00b75 cm [C12]") == {
        Decimal(1200),
        Decimal("2.1"),
        Decimal("1.5"),
    }
    assert numbers_in("1200.0") == numbers_in("1,200")


def test_bullets_written_on_one_line_are_separate_sentences() -> None:
    assert sentences("- First finding [C1]. - Second finding. * Third [C2].") == [
        "First finding [C1].",
        "Second finding.",
        "Third [C2].",
    ]
    assert sentences("Fell by 5 kg. -3 kg was the range [C1].") == [
        "Fell by 5 kg. -3 kg was the range [C1]."
    ]


def test_small_study_counts_are_exempt() -> None:
    assert checkable_numbers("Evidence from 3 randomized trials shows 5 kg gains [C1].") == {
        Decimal(5)
    }
    assert checkable_numbers("Across 120 trials [C1].") == {Decimal(120)}


def test_normalize_text_replaces_typographic_variants() -> None:
    assert normalize_text("meta\u2011analysis, 95\u202f%, a\u00a0b, x\u200by") == (
        "meta-analysis, 95 %, a b, xy"
    )


def test_strip_markers() -> None:
    assert strip_markers("Gains [C1, C2] were larger [C3].") == "Gains were larger ."
