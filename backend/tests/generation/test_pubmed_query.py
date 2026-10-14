from __future__ import annotations

from datetime import date

import pytest

from app.generation.pubmed_query import (
    RETRACTED_EXCLUSION,
    Terms,
    any_of,
    clean_term,
    compose,
    concept_clause,
    date_filter,
    natural_order,
    validate_query,
)


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("  dumbbell   curl* ", "dumbbell curl*"),
        ("dumbbell (curl)", "dumbbell curl"),
        ('elbow "flexor"', "elbow flexor"),
        ('curl" OR (biceps', None),
        ("()", None),
        ("x" * 81, None),
    ],
)
def test_clean_term(term: str, expected: str | None) -> None:
    assert clean_term(term) == expected


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Resistance Training", "resistance training"),
        ("Muscle, Skeletal", "skeletal muscle"),
        ("Diabetes Mellitus, Type 2", "type 2 diabetes mellitus"),
    ],
)
def test_natural_order(heading: str, expected: str) -> None:
    assert natural_order(heading) == expected


def test_every_mesh_heading_is_paired_with_title_abstract_terms() -> None:
    clause = concept_clause(Terms(mesh=["Muscle, Skeletal"], text=["hypertroph*"]))
    assert clause == '("Muscle, Skeletal"[mh] OR "hypertroph*"[tiab] OR "skeletal muscle"[tiab])'


def test_text_only_concept() -> None:
    assert concept_clause(Terms(text=["barbell*"])) == '("barbell*"[tiab])'


def test_empty_or_unsafe_concept_gives_no_clause() -> None:
    assert concept_clause(Terms()) is None
    assert concept_clause(Terms(text=["()", 'a" OR b'])) is None


def test_duplicate_terms_collapse_case_insensitively() -> None:
    clause = concept_clause(Terms(mesh=["Exercise"], text=["exercise", "EXERCISE"]))
    assert clause == '("Exercise"[mh] OR "exercise"[tiab])'


def test_compose_always_excludes_retractions() -> None:
    query = compose(['("x"[tiab])', '("y"[tiab])'], include=any_of(['"review"[pt]']))
    assert query == f'((("x"[tiab]) AND ("y"[tiab])) AND ("review"[pt])) {RETRACTED_EXCLUSION}'
    assert validate_query(query) == []


def test_compose_with_dates_and_exclusion_is_valid() -> None:
    query = compose(
        ['("x"[tiab])'],
        exclude=any_of(['"review"[pt]', '"comment"[pt]']),
        dates=date_filter(date(2026, 4, 14), None, "edat"),
    )
    assert "2026/04/14:3000[edat]" in query
    assert validate_query(query) == []


def test_date_filter_with_an_end() -> None:
    assert date_filter(date(2020, 1, 1), date(2024, 12, 31), "dp") == "2020/01/01:2024/12/31[dp]"


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ('("x"[tiab]', "unbalanced parentheses"),
        ('"x"[tiab])', "')' without matching '('"),
        ('"x[tiab]', "unbalanced quotes"),
        ('"x"[foo]', "unknown field tag [foo]"),
        ('AND "x"[tiab]', "starts with an operator"),
        ('"x"[tiab] OR', "ends with an operator"),
        ('"x"[tiab] AND OR "y"[tiab]', "two operators in a row"),
        ('"x"[tiab] AND ()', "empty parentheses"),
        ('(OR "x"[tiab])', "operator after '('"),
        ('("x"[tiab] AND)', "operator before ')'"),
        ("   ", "empty query"),
    ],
)
def test_validator_reports_each_problem(query: str, problem: str) -> None:
    assert problem in validate_query(query)


def test_brackets_and_operators_inside_quotes_are_ignored() -> None:
    assert validate_query('"rock (and) roll OR"[tiab]') == []


def test_check_tag_headings_are_not_echoed_as_text() -> None:
    clause = concept_clause(Terms(mesh=["Aged", "Humans"], text=["older adult*"]))
    assert clause == '("Aged"[mh] OR "Humans"[mh] OR "older adult*"[tiab])'
