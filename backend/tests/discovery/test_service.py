from __future__ import annotations

from collections.abc import Callable

import pytest

from app.config import Settings
from app.discovery.service import DiscoveryConfig, DiscoveryService
from app.errors import RateLimitedError
from app.generation.verify import Judge
from app.models.llm import LLMClient
from tests.discovery.builders import GLUCOSE, MUSCLE, TOPIC, FakeArticleSource, all_articles
from tests.generation.builders import stored
from tests.llm_fakes import Reply, ScriptedProvider

HAPPY = [
    stored("themes_two"),
    stored("digest_glucose"),
    stored("digest_muscle"),
    stored("judge_empty"),
]
Build = Callable[[list[Reply]], tuple[DiscoveryService, ScriptedProvider]]


@pytest.fixture
def service(make_settings: Callable[..., Settings]) -> Build:
    def _build(replies: list[Reply]) -> tuple[DiscoveryService, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        llm = LLMClient(make_settings(), provider)
        source = FakeArticleSource(all_articles())
        return DiscoveryService(llm, source, Judge(llm), DiscoveryConfig()), provider

    return _build


async def test_digest_has_named_themes_with_cited_articles(service: Build) -> None:
    built, provider = service(HAPPY)
    grouping = await built.group(TOPIC, [*GLUCOSE, *MUSCLE])
    assert [d.name for d in grouping.drafts] == [
        "Continuous glucose monitoring in the oldest patients",
        "GLP-1 drugs and muscle health",
    ]
    progress: list[tuple[int, int, str]] = []
    digest, result = await built.summarize(
        TOPIC, grouping, found=5900, progress=lambda i, n, name: progress.append((i, n, name))
    )
    assert result.passed
    assert result.judge_status == "ok"
    assert [len(t.articles) for t in digest.themes] == [3, 3]
    assert all(len({a.pmid for a in t.articles}) >= 2 for t in digest.themes)
    assert all(a.badge for t in digest.themes for a in t.articles)
    assert digest.themes[0].articles[0].marker == "C1"
    assert [c.marker for c in digest.citations] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    assert (digest.found, digest.considered) == (5900, 6)
    assert [p[0] for p in progress] == [1, 2]
    assert provider.systems[0].startswith("You name the themes")
    assert provider.systems[1].startswith("You write one theme")
    assert provider.systems[3].startswith("You check an evidence summary")


async def test_a_failed_summary_is_regenerated_once(service: Build) -> None:
    bad = stored("digest_glucose", summary="Monitoring helps older adults.")
    built, provider = service([HAPPY[0], bad, *HAPPY[1:]])
    grouping = await built.group(TOPIC, GLUCOSE + MUSCLE)
    digest, result = await built.summarize(TOPIC, grouping, found=10)
    assert result.passed
    assert "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS" in provider.prompts[2]
    assert digest.themes[0].summary.startswith("In adults over 75")


async def test_a_theme_that_cannot_be_written_is_left_out(service: Build) -> None:
    failure = RateLimitedError("slow down")
    built, _ = service(
        [stored("themes_two"), stored("digest_glucose"), failure, stored("judge_empty")]
    )
    digest, result = await built.summarize(
        TOPIC, await built.group(TOPIC, GLUCOSE + MUSCLE), found=10
    )
    assert [t.name for t in digest.themes] == [
        "Continuous glucose monitoring in the oldest patients"
    ]
    assert [i.kind for i in result.warnings] == ["theme_omitted"]
    assert result.passed


async def test_naming_failure_falls_back_to_plain_names(service: Build) -> None:
    built, _ = service(["not json", "still not json"])
    grouping = await built.group(TOPIC, GLUCOSE + MUSCLE)
    assert [d.name for d in grouping.drafts] == ["Related research 1", "Related research 2"]
    assert grouping.named is False


async def test_unsupported_claims_fail_verification(service: Build) -> None:
    verdict = stored(
        "judge_empty", claims=[{"id": "S2", "verdict": "unsupported", "note": "not in C2"}]
    )
    built, _ = service([*HAPPY[:3], verdict])
    grouping = await built.group(TOPIC, GLUCOSE + MUSCLE)
    _, result = await built.summarize(TOPIC, grouping, found=1)
    assert not result.passed
    assert [i.kind for i in result.errors] == ["unsupported_claim"]


async def test_too_few_articles_make_no_themes(service: Build) -> None:
    built, provider = service([])
    grouping = await built.group(TOPIC, [GLUCOSE[0]])
    assert grouping.drafts == []
    assert provider.prompts == []
