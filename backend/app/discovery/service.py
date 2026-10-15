"""Build a digest of recent research: search, group, name, summarize, verify."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np

from app.config import Settings
from app.db.queries.discovery import DiscoveryArticle
from app.discovery.clusters import cluster_vectors
from app.discovery.digest import Digest, Theme, ThemeArticle
from app.discovery.search import DiscoveryHits, Searcher, find_articles
from app.discovery.themes import (
    NAMING_PROMPT,
    SUMMARY_PROMPT,
    ThemeContext,
    ThemeDraft,
    build_contexts,
    check_summary,
    evidence_mix,
    name_clusters,
    render_digest_judge_request,
    summarize_theme,
    summary_claims,
    unnamed,
)
from app.errors import AppError
from app.generation.answer import (
    Citation,
    Issue,
    IssueKind,
    JudgeStatus,
    VerificationResult,
    issue,
)
from app.generation.context import ContextItem
from app.generation.plan import Plan
from app.generation.verify import JUDGE_PROMPT, Judge
from app.models.llm import LLMClient, UsageMeter

logger = logging.getLogger(__name__)

Progress = Callable[[int, int, str], None]


class ArticleSource(Protocol):
    async def load(self, pmids: Sequence[int]) -> list[DiscoveryArticle]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    max_articles: int = 50
    cache_ttl_s: int = 3600
    max_themes: int = 8
    articles_per_theme: int = 6
    summary_max_tokens: int = 3000
    naming_max_tokens: int = 1000


def discovery_config(settings: Settings) -> DiscoveryConfig:
    return DiscoveryConfig(
        max_articles=settings.discovery_max_articles,
        cache_ttl_s=settings.ncbi_discovery_cache_ttl_s,
        max_themes=settings.discovery_max_themes,
        articles_per_theme=settings.discovery_articles_per_theme,
        summary_max_tokens=settings.discovery_summary_max_tokens,
    )


@dataclass(frozen=True, slots=True)
class Grouping:
    drafts: list[ThemeDraft]
    considered: int  # articles with abstracts
    named: bool


class DiscoveryService:
    def __init__(
        self,
        llm: LLMClient,
        articles: ArticleSource,
        judge: Judge | None,
        config: DiscoveryConfig | None = None,
    ) -> None:
        self._llm = llm
        self._articles = articles
        self._judge = judge
        self.config = config or DiscoveryConfig()

    async def search(
        self, searcher: Searcher, plan: Plan, *, today: date | None = None
    ) -> DiscoveryHits:
        if not plan.tiers:
            return DiscoveryHits(pmids=[], total=0, slices=[])
        return await find_articles(
            searcher,
            plan.tiers[0].pubmed_query,
            plan.date_range,
            cap=self.config.max_articles,
            cache_ttl_s=self.config.cache_ttl_s,
            today=today,
        )

    async def group(
        self, topic: str, pmids: Sequence[int], meter: UsageMeter | None = None
    ) -> Grouping:
        articles = await self._articles.load(pmids)
        if not articles:
            return Grouping(drafts=[], considered=0, named=True)
        indices = cluster_vectors(
            np.stack([a.vector for a in articles]),
            per_theme=self.config.articles_per_theme,
            max_clusters=self.config.max_themes,
        )
        clusters = [[articles[i] for i in members] for members in indices]
        if not clusters:
            return Grouping(drafts=[], considered=len(articles), named=True)
        try:
            drafts = await name_clusters(
                self._llm, topic, clusters, max_tokens=self.config.naming_max_tokens, meter=meter
            )
            named = True
        except AppError as exc:
            logger.warning("theme_naming_failed", extra={"reason": exc.message})
            drafts, named = unnamed(clusters), False
        kept = drafts[: self.config.max_themes]
        return Grouping(drafts=kept, considered=len(articles), named=named)

    async def summarize(
        self,
        topic: str,
        grouping: Grouping,
        *,
        found: int,
        plan: Plan | None = None,
        meter: UsageMeter | None = None,
        progress: Progress | None = None,
    ) -> tuple[Digest, VerificationResult]:
        """One generator call per theme, regenerated once on failed checks; a theme whose
        calls fail is left out with a warning. Then one judge call over every summary.
        """
        contexts = build_contexts(grouping.drafts, self.config.articles_per_theme)
        issues: list[Issue] = []
        written: dict[str, tuple[str, ThemeContext]] = {}
        last_error: AppError | None = None
        for index, context in enumerate(contexts, start=1):
            if progress is not None:
                progress(index, len(contexts), context.name)
            try:
                summary, problems = await self._write(topic, context, meter)
            except AppError as exc:
                logger.warning(
                    "theme_omitted", extra={"theme": context.name, "reason": exc.message}
                )
                last_error = exc
                issues.append(
                    issue("theme_omitted", f"theme {context.name}", f"not written: {exc.message}")
                )
                continue
            written[context.name] = (summary, context)
            issues += problems
        if contexts and not written and last_error is not None:
            raise last_error

        status, judged = await self._judge_summaries(topic, written, meter)
        issues += judged
        window = plan.date_range if plan else None
        themes = [_theme(name, summary, context) for name, (summary, context) in written.items()]
        digest = Digest(
            topic=topic,
            window_start=window.start if window else None,
            window_end=window.end if window else None,
            found=found,
            considered=grouping.considered,
            themes=themes,
            citations=[
                _citation(item) for _, context in written.values() for item in context.items
            ],
            prompts=[NAMING_PROMPT.id, SUMMARY_PROMPT.id],
        )
        result = VerificationResult(
            passed=not any(i.severity == "error" for i in issues),
            issues=issues,
            judge_status=status,
            attempts=1,
            judge_prompt=JUDGE_PROMPT.id if status != "skipped" else None,
        )
        return digest, result

    async def _write(
        self, topic: str, context: ThemeContext, meter: UsageMeter | None
    ) -> tuple[str, list[Issue]]:
        summary = await summarize_theme(
            self._llm, topic, context, max_tokens=self.config.summary_max_tokens, meter=meter
        )
        problems = check_summary(summary, context)
        if not any(p.severity == "error" for p in problems):
            return summary, problems
        retry = await summarize_theme(
            self._llm,
            topic,
            context,
            max_tokens=self.config.summary_max_tokens,
            feedback=[p for p in problems if p.severity == "error"],
            meter=meter,
        )
        return retry, check_summary(retry, context)

    async def _judge_summaries(
        self,
        topic: str,
        written: dict[str, tuple[str, ThemeContext]],
        meter: UsageMeter | None,
    ) -> tuple[JudgeStatus, list[Issue]]:
        """Fails open, as for answers: the deterministic checks already ran."""
        claims = summary_claims(written)
        if self._judge is None or not claims:
            return "skipped", []
        contexts = [context for _, context in written.values()]
        try:
            verdict = await self._judge.assess(
                render_digest_judge_request(topic, claims, contexts), meter
            )
        except AppError as exc:
            logger.warning("digest_judge_failed", extra={"reason": exc.message})
            return "failed", [
                issue("judge_unavailable", "digest", f"the judge could not run: {exc.message}")
            ]
        by_id = {claim.id: claim for claim in claims}
        found: list[Issue] = []
        for item in verdict.claims:
            claim = by_id.get(item.id)
            if claim is None or item.verdict == "supported":
                continue
            kind: IssueKind = (
                "unsupported_claim" if item.verdict == "unsupported" else "partially_supported"
            )
            reason = item.note or f"not supported by {', '.join(claim.markers)}"
            found.append(issue(kind, claim.location, reason, claim_id=claim.id))
        return "ok", found


def _theme(name: str, summary: str, context: ThemeContext) -> Theme:
    return Theme(
        name=name,
        summary=summary,
        evidence_mix=evidence_mix(context.items),
        articles=[
            ThemeArticle(
                pmid=item.chunk.pmid,
                title=item.chunk.title,
                design=item.passage.grade.design,
                badge=item.passage.grade.badge,
                year=item.chunk.pub_date.year if item.chunk.pub_date else None,
                marker=item.marker,
            )
            for item in context.items
        ],
        more_articles=context.more,
    )


def _citation(item: ContextItem) -> Citation:
    return Citation(
        marker=item.marker,
        pmid=item.chunk.pmid,
        section=item.chunk.section,
        chunk_id=item.chunk.chunk_id,
        snippet=item.text,
    )
