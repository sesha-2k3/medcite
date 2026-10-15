from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.eval.metrics import (
    EvalQuestion,
    QuestionResult,
    compare,
    found_by_search,
    load_dataset,
    load_results,
    pending,
    recall_at_k,
    reciprocal_rank,
    render_markdown,
    summarize,
    write_summary,
)

DATASET = Path(__file__).resolve().parents[3] / "eval" / "datasets" / "smoke.jsonl"


def result(qid: str, **changes: Any) -> QuestionResult:
    base: dict[str, Any] = {
        "id": qid,
        "kind": "direct",
        "question": f"question {qid}",
        "expected_gap": False,
        "gold_pmids": [1, 2],
        "reported_gap": False,
        "broadened": False,
        "candidate_pmids": [1, 2, 3],
        "ranked_pmids": [3, 1, 4],
        "cited_pmids": [1],
        "verification_passed": True,
        "judge_status": "ok",
        "attempts": 1,
        "issue_kinds": [],
        "latency_ms": 20_000,
        "tokens_by_model": {"gen": 15_000, "plan": 5_000},
    }
    return QuestionResult(**{**base, **changes})


def test_retrieval_metrics() -> None:
    assert found_by_search([1, 2], [2, 9]) == 0.5
    assert recall_at_k([1, 2], [5, 1, 2], k=2) == 0.5
    assert reciprocal_rank([1, 2], [5, 6, 2]) == pytest.approx(1 / 3)
    assert reciprocal_rank([1], [5, 6]) == 0.0


def test_summary_counts_gaps_and_ignores_unlabeled_rows_for_retrieval() -> None:
    rows = [
        result("a"),
        result("b", gold_pmids=[], kind="gap", expected_gap=True, reported_gap=False),
        result("c", kind="false_gap", reported_gap=True, verification_passed=False, attempts=2),
    ]
    summary = summarize(rows)
    assert summary.labeled == 2
    assert summary.recall_at_10 == 0.5
    assert summary.mrr == 0.5
    assert summary.gap_accuracy == pytest.approx(1 / 3)
    assert summary.false_gaps == ["c"]
    assert summary.missed_gaps == ["b"]
    assert summary.verification_pass_rate == pytest.approx(2 / 3)
    assert summary.retried == 1
    assert summary.tokens_by_model == {"gen": 45_000, "plan": 15_000}
    assert summary.median_latency_s == 20.0


def test_digest_and_out_of_scope_rows_are_left_out_of_gap_accuracy() -> None:
    summary = summarize([result("a"), result("b", reported_gap=None)])
    assert summary.gap_accuracy == 1.0


def test_an_empty_report_summarizes_to_nothing() -> None:
    summary = summarize([])
    assert summary.questions == 0
    assert summary.recall_at_10 is None
    assert summary.median_latency_s is None


def test_pending_skips_finished_questions() -> None:
    rows = [
        EvalQuestion(id="a", kind="direct", question="q", expected_gap=False),
        EvalQuestion(id="b", kind="gap", question="q", expected_gap=True),
    ]
    assert [row.id for row in pending(rows, [result("a")])] == ["b"]


def test_dataset_rejects_duplicate_ids(tmp_path: Path) -> None:
    line = '{"id": "a", "kind": "gap", "question": "q", "expected_gap": true}'
    path = tmp_path / "set.jsonl"
    path.write_text(f"{line}\n\n{line}\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(path)


def test_the_committed_dataset_is_valid() -> None:
    rows = load_dataset(DATASET)
    assert len(rows) == 20
    assert {row.kind for row in rows} == {"direct", "gap", "false_gap"}
    assert all(row.expected_gap for row in rows if row.kind == "gap")
    assert not any(row.expected_gap for row in rows if row.kind != "gap")
    assert all(not row.gold_pmids for row in rows if row.kind == "gap")


def test_reports_round_trip_and_render(tmp_path: Path) -> None:
    rows = [result("a"), result("b", gold_pmids=[], kind="gap", expected_gap=True)]
    (tmp_path / "r.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))
    assert load_results(tmp_path / "r.jsonl") == rows
    assert load_results(tmp_path / "missing.jsonl") == []
    write_summary(tmp_path, "r", rows)
    markdown = (tmp_path / "r.md").read_text()
    assert "| a | direct | no | no | 50% | 0.50 | yes | 20,000 |" in markdown
    assert "| b | gap | yes | no | - | - | yes | 20,000 |" in markdown
    assert (tmp_path / "r.json").exists()
    assert render_markdown("r", summarize(rows), rows).startswith("# Evaluation report: r")


def test_compare_reports_metric_deltas_and_flipped_outcomes() -> None:
    before = [result("a"), result("b", reported_gap=True)]
    after = [result("a", ranked_pmids=[1, 2]), result("b", reported_gap=False)]
    lines = compare(before, after)
    assert any(line.startswith("recall_at_10") and "(+0.250)" in line for line in lines)
    assert "b: gap yes -> no" in lines
