"""Name clusters, summarize each theme with citations, and check the summaries.

Naming is one call on the planner model. Summaries are one generator call per theme, which
keeps each call far below the free plan's per-minute token limit. Code assigns the [C#]
markers, computes each theme's evidence mix, and checks citations and numbers with M6's rules.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.db.queries.discovery import DiscoveryArticle
from app.discovery.digest import SummaryDraft, ThemeNames
from app.generation.answer import Issue, issue
from app.generation.citations import (
    checkable_numbers,
    marker_ids,
    normalize_text,
    sentences,
    strip_markers,
)
from app.generation.context import DESIGN_LABELS, ContextItem, section_label, trim_text
from app.generation.prompts import load_prompt
from app.generation.verify import Claim, source_numbers
from app.models.llm import ChatMessage, LLMClient, LLMRole, UsageMeter
from app.retrieval.evidence import grade_study
from app.retrieval.hybrid_search import HybridHit
from app.retrieval.rerank import ScoredChunk
from app.retrieval.retriever import EvidencePassage

NAMING_PROMPT = load_prompt("themes", "v1")
SUMMARY_PROMPT = load_prompt("digest", "v1")
TITLES_PER_CLUSTER = 8
MAX_NAME_CHARS = 80
# Words a shortened name must not end on.
_DANGLING = frozenset(
    [
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "versus",
        "vs",
        "with",
    ]
)
PASSAGE_CHARS = 700
MAX_FEEDBACK_ITEMS = 8


@dataclass(frozen=True, slots=True)
class ThemeDraft:
    name: str
    members: list[DiscoveryArticle]
    named: bool  # False when the name is a fallback


@dataclass(frozen=True, slots=True)
class ThemeContext:
    name: str
    items: list[ContextItem]
    more: int
    text: str

    @property
    def by_marker(self) -> dict[str, ContextItem]:
        return {item.marker: item for item in self.items}


def render_naming_request(topic: str, clusters: Sequence[Sequence[DiscoveryArticle]]) -> str:
    lines = [f"TOPIC: {topic}", "CLUSTERS:"]
    for index, members in enumerate(clusters, start=1):
        lines.append(f"K{index} ({len(members)} articles):")
        lines += [f"- {m.passage.title}" for m in members[:TITLES_PER_CLUSTER]]
    return "\n".join(lines)


def apply_names(names: ThemeNames, clusters: Sequence[list[DiscoveryArticle]]) -> list[ThemeDraft]:
    """Groups the model named (merging near-duplicates), then any cluster it skipped, each
    cluster used once; largest themes first.
    """
    valid = {f"K{i}": members for i, members in enumerate(clusters, start=1)}
    used: set[str] = set()
    drafts: list[ThemeDraft] = []
    for group in names.themes:
        ids = [k for k in dict.fromkeys(group.clusters) if k in valid and k not in used]
        if not ids:
            continue
        used.update(ids)
        members = [m for k in ids for m in valid[k]]
        name = clean_name(group.name)
        drafts.append(ThemeDraft(name or fallback_name(len(drafts) + 1), members, bool(name)))
    for key, members in valid.items():
        if key not in used:
            drafts.append(ThemeDraft(fallback_name(len(drafts) + 1), list(members), False))
    return sorted(drafts, key=lambda d: -len(d.members))


def clean_name(raw: str) -> str:
    name = " ".join(normalize_text(raw).split()).strip(" .:;-\"'")
    if len(name) <= MAX_NAME_CHARS:
        return name
    window = name[: MAX_NAME_CHARS + 1]  # a space just past the limit ends a whole word
    if " " not in window:
        return name[:MAX_NAME_CHARS]
    words = window.rsplit(" ", 1)[0].split(" ")
    while len(words) > 1 and words[-1].rstrip(",;:-").lower() in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:-")


def fallback_name(index: int) -> str:
    return f"Related research {index}"


def unnamed(clusters: Sequence[list[DiscoveryArticle]]) -> list[ThemeDraft]:
    return [
        ThemeDraft(fallback_name(i), list(members), False)
        for i, members in enumerate(clusters, start=1)
    ]


async def name_clusters(
    llm: LLMClient,
    topic: str,
    clusters: Sequence[list[DiscoveryArticle]],
    *,
    max_tokens: int,
    meter: UsageMeter | None = None,
) -> list[ThemeDraft]:
    """Raises on LLM failure; the caller falls back to ``unnamed``."""
    messages = [
        ChatMessage("system", NAMING_PROMPT.text),
        ChatMessage("user", render_naming_request(topic, clusters)),
    ]
    names = await llm.complete_json(
        LLMRole.PLANNER, messages, ThemeNames, max_tokens=max_tokens, meter=meter
    )
    return apply_names(names, clusters)


def build_contexts(drafts: Sequence[ThemeDraft], per_theme: int) -> list[ThemeContext]:
    """Markers run across the whole digest, one per article, in theme order."""
    contexts: list[ThemeContext] = []
    counter = 0
    for draft in drafts:
        items: list[ContextItem] = []
        for article in draft.members[:per_theme]:
            counter += 1
            items.append(_item(f"C{counter}", article))
        contexts.append(
            ThemeContext(
                name=draft.name,
                items=items,
                more=max(0, len(draft.members) - per_theme),
                text="\n".join(_passage_lines(item) for item in items),
            )
        )
    return contexts


def _item(marker: str, article: DiscoveryArticle) -> ContextItem:
    chunk = article.passage
    passage = EvidencePassage(
        scored=ScoredChunk(
            chunk=chunk,
            hit=HybridHit(
                chunk_id=chunk.chunk_id, rrf_score=0.0, dense_rank=None, sparse_rank=None
            ),
            relevance=0.0,
        ),
        grade=grade_study(chunk.study_meta, publication_status=chunk.publication_status),
        tier=None,
    )
    return ContextItem(marker=marker, passage=passage, text=trim_text(chunk.content, PASSAGE_CHARS))


def _passage_lines(item: ContextItem) -> str:
    chunk, grade = item.chunk, item.passage.grade
    parts = [f"[{item.marker}] PMID {chunk.pmid}", DESIGN_LABELS[grade.design]]
    if chunk.study_meta.sample_size is not None:
        parts.append(f"n={chunk.study_meta.sample_size}")
    if chunk.pub_date is not None:
        parts.append(str(chunk.pub_date.year))
    if grade.modifiers:
        parts.append("flags: " + ", ".join(m.replace("_", " ") for m in grade.modifiers))
    header = " | ".join(parts)
    return f"{header}\nTitle: {chunk.title}\n{section_label(chunk.section)}: {item.text}\n"


def evidence_mix(items: Sequence[ContextItem]) -> str:
    """Counts by badge, strongest evidence first: '1 RCT, 3 Observational'."""
    levels = {item.passage.grade.badge: item.passage.grade.level for item in items}
    counts = Counter(item.passage.grade.badge for item in items)
    ordered = sorted(counts, key=lambda badge: (levels[badge], badge))
    return ", ".join(f"{counts[badge]} {badge}" for badge in ordered)


def render_summary_request(
    topic: str, context: ThemeContext, feedback: Sequence[Issue] = ()
) -> str:
    lines = [
        f"TOPIC: {topic}",
        f"THEME: {context.name}",
        f"EVIDENCE MIX: {evidence_mix(context.items)}",
    ]
    if feedback:
        lines.append("YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS. Fix every one:")
        lines += [f"- {item.message}" for item in feedback[:MAX_FEEDBACK_ITEMS]]
    lines += ["", "PASSAGES:", context.text]
    return "\n".join(lines)


async def summarize_theme(
    llm: LLMClient,
    topic: str,
    context: ThemeContext,
    *,
    max_tokens: int,
    feedback: Sequence[Issue] = (),
    meter: UsageMeter | None = None,
) -> str:
    messages = [
        ChatMessage("system", SUMMARY_PROMPT.text),
        ChatMessage("user", render_summary_request(topic, context, feedback)),
    ]
    draft = await llm.complete_json(
        LLMRole.GENERATOR, messages, SummaryDraft, max_tokens=max_tokens, meter=meter
    )
    return normalize_text(draft.summary).strip()


def check_summary(summary: str, context: ThemeContext) -> list[Issue]:
    """M6's rules: known markers, a citation on every sentence, numbers from the sources."""
    location = f"theme {context.name}"
    known = context.by_marker
    issues: list[Issue] = []
    found = sentences(summary)
    if not found:
        issues.append(issue("uncited_sentence", location, "the summary is empty"))
    for sentence in found:
        markers = marker_ids(sentence)
        unknown = [m for m in markers if m not in known]
        if unknown:
            issues.append(
                issue(
                    "unknown_citation",
                    location,
                    f"cites {', '.join(unknown)}, not one of this theme's passages: "
                    f"{strip_markers(sentence)}",
                )
            )
        if not markers:
            issues.append(
                issue(
                    "uncited_sentence",
                    location,
                    "no citation; end it with the markers of the passages it is about: "
                    f"{strip_markers(sentence)}",
                )
            )
            continue
        cited = [known[m] for m in markers if m in known]
        missing = checkable_numbers(sentence) - source_numbers(cited)
        if cited and missing:
            shown = ", ".join(sorted(format(n, "f") for n in missing))
            issues.append(
                issue(
                    "number_not_in_source",
                    location,
                    f"{shown} not found in {', '.join(markers)}: {strip_markers(sentence)}",
                )
            )
    return issues


def summary_claims(summaries: Mapping[str, tuple[str, ThemeContext]]) -> list[Claim]:
    claims: list[Claim] = []
    for name, (summary, _) in summaries.items():
        for sentence in sentences(summary):
            if markers := marker_ids(sentence):
                claims.append(
                    Claim(f"S{len(claims) + 1}", f"theme {name}", sentence, tuple(markers))
                )
    return claims


def render_digest_judge_request(
    topic: str, claims: Sequence[Claim], contexts: Sequence[ThemeContext]
) -> str:
    lines = [f"QUESTION: Recent research on {topic}", "EVIDENCE GAP: no", "CLAIMS:"]
    lines += [f"{c.id} [{', '.join(c.markers)}] {strip_markers(c.text)}" for c in claims]
    lines += ["", "PASSAGES:", *(context.text for context in contexts)]
    return "\n".join(lines)
