from __future__ import annotations

from app.discovery.digest import ThemeGroup, ThemeNames
from app.discovery.themes import (
    ThemeContext,
    ThemeDraft,
    apply_names,
    build_contexts,
    check_summary,
    clean_name,
    evidence_mix,
    render_naming_request,
)
from app.retrieval.evidence import grade_study
from tests.discovery.builders import GLUCOSE, MUSCLE, TOPIC, article
from tests.generation.builders import stored_data

GLUCOSE_ARTICLES = [article(p) for p in GLUCOSE]
MUSCLE_ARTICLES = [article(p) for p in MUSCLE]


def names(*groups: tuple[list[str], str]) -> ThemeNames:
    return ThemeNames(themes=[ThemeGroup(clusters=c, name=n) for c, n in groups])


def test_naming_request_lists_titles_per_cluster() -> None:
    text = render_naming_request(TOPIC, [GLUCOSE_ARTICLES, MUSCLE_ARTICLES])
    assert text.startswith(f"TOPIC: {TOPIC}\nCLUSTERS:\nK1 (3 articles):")
    assert "K2 (3 articles):\n- Lean mass loss with GLP-1" in text


def test_merges_and_skipped_clusters() -> None:
    third = [article(GLUCOSE[0])]
    drafts = apply_names(
        names((["K1", "k3"], "Glucose monitoring"), (["K9"], "Invented"), (["K1"], "Again")),
        [GLUCOSE_ARTICLES, MUSCLE_ARTICLES, third],
    )
    assert [(d.name, len(d.members), d.named) for d in drafts] == [
        ("Glucose monitoring", 4, True),
        ("Related research 2", 3, False),
    ]


def test_cluster_ids_may_be_numbers() -> None:
    group = ThemeGroup(clusters=[1, "k2 "], name="x")  # type: ignore[list-item]
    assert group.clusters == ["K1", "K2"]


def test_names_are_cleaned() -> None:
    assert clean_name('  "GLP-1 drugs\u2011and   muscle."  ') == "GLP-1 drugs-and muscle"


def test_markers_run_across_themes_and_extra_members_are_counted() -> None:
    drafts = [
        ThemeDraft("Glucose", GLUCOSE_ARTICLES, True),
        ThemeDraft("Muscle", MUSCLE_ARTICLES, True),
    ]
    first, second = build_contexts(drafts, per_theme=2)
    assert [i.marker for i in first.items] == ["C1", "C2"]
    assert [i.marker for i in second.items] == ["C3", "C4"]
    assert (first.more, second.more) == (1, 1)
    assert first.text.startswith("[C1] PMID 90000301 | RCT | n=120 | 2026\nTitle: Continuous")
    assert "CONCLUSIONS: Continuous glucose monitoring reduced" in first.text


def test_evidence_mix_lists_the_strongest_evidence_first() -> None:
    [context] = build_contexts([ThemeDraft("Glucose", GLUCOSE_ARTICLES, True)], per_theme=6)
    badges = [grade_study(a.passage.study_meta).badge for a in GLUCOSE_ARTICLES]
    mix = evidence_mix(context.items)
    assert mix.startswith(f"1 {badges[0]}")  # the RCT
    assert all(f"1 {badge}" in mix for badge in badges)


def contexts() -> list[ThemeContext]:
    drafts = [
        ThemeDraft("Glucose", GLUCOSE_ARTICLES, True),
        ThemeDraft("Muscle", MUSCLE_ARTICLES, True),
    ]
    return build_contexts(drafts, per_theme=6)


def test_a_well_cited_summary_passes() -> None:
    glucose, muscle = contexts()
    assert check_summary(stored_data("digest_glucose")["summary"], glucose) == []
    assert check_summary(stored_data("digest_muscle")["summary"], muscle) == []


def test_citing_another_themes_article_fails() -> None:
    glucose, _ = contexts()
    [problem] = check_summary("Lean mass fell in drug users [C4].", glucose)
    assert problem.kind == "unknown_citation"
    assert problem.location == "theme Glucose"


def test_uncited_and_invented_numbers_fail() -> None:
    glucose, _ = contexts()
    problems = check_summary(
        "Monitoring cut hypoglycemia by 5.5 hours [C1]. Use is growing quickly.", glucose
    )
    assert [p.kind for p in problems] == ["number_not_in_source", "uncited_sentence"]


def test_an_empty_summary_fails() -> None:
    glucose, _ = contexts()
    assert [p.kind for p in check_summary("", glucose)] == ["uncited_sentence"]
