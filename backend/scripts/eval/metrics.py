"""Evaluation data, metrics and reports. Pure: no network, models or database.

A dataset row is one question with its expected gap outcome and, for direct questions, gold
PMIDs a good answer should retrieve. A result row is what one pipeline run produced for it.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["direct", "gap", "false_gap"]
TOP_K = 10


class EvalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    kind: Kind
    question: str
    expected_gap: bool
    gold_pmids: list[int] = Field(default_factory=list)
    note: str = ""


class QuestionResult(BaseModel):
    id: str
    kind: Kind
    question: str
    expected_gap: bool
    gold_pmids: list[int]
    reported_gap: bool | None
    broadened: bool
    candidate_pmids: list[int]
    ranked_pmids: list[int] = Field(description="Articles in context order, best first")
    cited_pmids: list[int]
    verification_passed: bool | None
    judge_status: str | None
    attempts: int
    issue_kinds: list[str]
    latency_ms: int
    tokens_by_model: dict[str, int]

    @property
    def tokens(self) -> int:
        return sum(self.tokens_by_model.values())


class Summary(BaseModel):
    questions: int
    labeled: int
    found_by_search: float | None
    recall_at_10: float | None
    mrr: float | None
    gap_accuracy: float | None
    false_gaps: list[str]
    missed_gaps: list[str]
    verification_pass_rate: float | None
    judge_unavailable: int
    retried: int
    median_latency_s: float | None
    mean_tokens: float | None
    tokens_by_model: dict[str, int]


def load_dataset(path: Path) -> list[EvalQuestion]:
    """Rows from a JSONL file; blank lines are skipped and ids must be unique."""
    rows = [
        EvalQuestion.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    ids = [row.id for row in rows]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate question ids: {', '.join(duplicates)}")
    return rows


def load_results(path: Path) -> list[QuestionResult]:
    if not path.exists():
        return []
    return [
        QuestionResult.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def pending(dataset: Sequence[EvalQuestion], done: Iterable[QuestionResult]) -> list[EvalQuestion]:
    finished = {result.id for result in done}
    return [row for row in dataset if row.id not in finished]


def found_by_search(gold: Sequence[int], candidates: Sequence[int]) -> float:
    """Share of gold PMIDs that PubMed returned at all."""
    return len(set(gold) & set(candidates)) / len(gold)


def recall_at_k(gold: Sequence[int], ranked: Sequence[int], k: int = TOP_K) -> float:
    return len(set(gold) & set(ranked[:k])) / len(gold)


def reciprocal_rank(gold: Sequence[int], ranked: Sequence[int]) -> float:
    wanted = set(gold)
    return next((1 / rank for rank, pmid in enumerate(ranked, 1) if pmid in wanted), 0.0)


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarize(results: Sequence[QuestionResult]) -> Summary:
    """Retrieval metrics use only rows with gold PMIDs; gap metrics use every row."""
    labeled = [r for r in results if r.gold_pmids]
    answered = [r for r in results if r.reported_gap is not None]
    verified = [r for r in results if r.verification_passed is not None]
    tokens: dict[str, int] = {}
    for result in results:
        for model, count in result.tokens_by_model.items():
            tokens[model] = tokens.get(model, 0) + count
    return Summary(
        questions=len(results),
        labeled=len(labeled),
        found_by_search=_mean([found_by_search(r.gold_pmids, r.candidate_pmids) for r in labeled]),
        recall_at_10=_mean([recall_at_k(r.gold_pmids, r.ranked_pmids) for r in labeled]),
        mrr=_mean([reciprocal_rank(r.gold_pmids, r.ranked_pmids) for r in labeled]),
        gap_accuracy=_mean([float(r.reported_gap == r.expected_gap) for r in answered]),
        false_gaps=[r.id for r in answered if r.reported_gap and not r.expected_gap],
        missed_gaps=[r.id for r in answered if r.expected_gap and not r.reported_gap],
        verification_pass_rate=_mean([float(bool(r.verification_passed)) for r in verified]),
        judge_unavailable=sum(r.judge_status == "unavailable" for r in results),
        retried=sum(r.attempts > 1 for r in results),
        median_latency_s=(
            statistics.median(r.latency_ms for r in results) / 1000 if results else None
        ),
        mean_tokens=_mean([r.tokens for r in results]),
        tokens_by_model=tokens,
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _yes(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def render_markdown(name: str, summary: Summary, results: Sequence[QuestionResult]) -> str:
    lines = [
        f"# Evaluation report: {name}",
        "",
        f"{summary.questions} questions, {summary.labeled} with gold PMIDs.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Gold found by PubMed search | {_pct(summary.found_by_search)} |",
        f"| Recall@{TOP_K} (context articles) | {_pct(summary.recall_at_10)} |",
        f"| MRR | {_num(summary.mrr)} |",
        f"| Gap accuracy | {_pct(summary.gap_accuracy)} |",
        f"| False gaps | {', '.join(summary.false_gaps) or 'none'} |",
        f"| Missed gaps | {', '.join(summary.missed_gaps) or 'none'} |",
        f"| Verification pass rate | {_pct(summary.verification_pass_rate)} |",
        f"| Judge unavailable | {summary.judge_unavailable} |",
        f"| Answers revised once | {summary.retried} |",
        f"| Median latency (s) | {_num(summary.median_latency_s, 1)} |",
        f"| Mean tokens per answer | {_num(summary.mean_tokens, 0)} |",
        "",
        "Tokens by model: "
        + (", ".join(f"{m} {n:,}" for m, n in sorted(summary.tokens_by_model.items())) or "none"),
        "",
        "| Id | Kind | Gap expected | Gap reported | Recall@10 | RR | Verified | Tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        recall = _pct(recall_at_k(r.gold_pmids, r.ranked_pmids)) if r.gold_pmids else "-"
        rr = _num(reciprocal_rank(r.gold_pmids, r.ranked_pmids)) if r.gold_pmids else "-"
        lines.append(
            f"| {r.id} | {r.kind} | {_yes(r.expected_gap)} | {_yes(r.reported_gap)} | {recall} "
            f"| {rr} | {_yes(r.verification_passed)} | {r.tokens:,} |"
        )
    return "\n".join(lines) + "\n"


COMPARED: tuple[str, ...] = (
    "found_by_search",
    "recall_at_10",
    "mrr",
    "gap_accuracy",
    "verification_pass_rate",
    "median_latency_s",
    "mean_tokens",
)


def compare(before: Sequence[QuestionResult], after: Sequence[QuestionResult]) -> list[str]:
    """Metric deltas, then per-question changes in gap or verification outcome."""
    old, new = summarize(before), summarize(after)
    lines = []
    for field in COMPARED:
        a, b = getattr(old, field), getattr(new, field)
        delta = "n/a" if a is None or b is None else f"{b - a:+.3f}"
        lines.append(f"{field:<24} {_num(a, 3):>9} -> {_num(b, 3):>9}  ({delta})")
    earlier = {r.id: r for r in before}
    for r in after:
        prior = earlier.get(r.id)
        if prior is None:
            continue
        if prior.reported_gap != r.reported_gap:
            lines.append(f"{r.id}: gap {_yes(prior.reported_gap)} -> {_yes(r.reported_gap)}")
        if prior.verification_passed != r.verification_passed:
            lines.append(
                f"{r.id}: verified {_yes(prior.verification_passed)} -> "
                f"{_yes(r.verification_passed)}"
            )
    return lines


def write_summary(directory: Path, name: str, results: Sequence[QuestionResult]) -> Summary:
    """Rewrite NAME.json and NAME.md from the result rows."""
    summary = summarize(results)
    payload = {
        "name": name,
        "summary": summary.model_dump(),
        "results": [r.model_dump() for r in results],
    }
    (directory / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (directory / f"{name}.md").write_text(render_markdown(name, summary, results))
    return summary
