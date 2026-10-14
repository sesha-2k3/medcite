from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import pytest

from app.config import ReasoningEffort, Settings
from app.errors import RateLimitedError
from app.ingest.metadata import MetadataExtractor, design_from_pub_types
from app.ingest.pubmed_parser import ParsedArticle
from app.models.llm import ChatMessage, LLMClient, ProviderResult
from tests.ingest.helpers import edge_cases

Reply = str | Exception


class ScriptedProvider:
    def __init__(self, replies: list[Reply]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderResult:
        self.prompts.append(messages[-1].content)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ProviderResult(text=reply, input_tokens=100, output_tokens=50)


BuildExtractor = Callable[..., tuple[MetadataExtractor, ScriptedProvider]]


def batch(*items: dict[str, object]) -> str:
    return json.dumps({"articles": list(items)})


@pytest.fixture
def articles() -> dict[int, ParsedArticle]:
    return edge_cases()


@pytest.fixture
def extractor(make_settings: Callable[..., Settings]) -> BuildExtractor:
    def _build(
        replies: list[Reply], batch_size: int = 5
    ) -> tuple[MetadataExtractor, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        return MetadataExtractor(LLMClient(make_settings(), provider), batch_size), provider

    return _build


async def test_llm_fills_fields_and_pub_types_decide_design(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    rct, unstructured = articles[90000101], articles[90000102]
    ex, provider = extractor(
        [
            batch(
                {
                    "pmid": rct.pmid,
                    "design": "cohort",
                    "sample_size": 24,
                    "age_group": "adults",
                    "outcome_class": "physiological",
                    "primary_outcome": "muscle thickness",
                },
                {"pmid": unstructured.pmid, "design": "cross_sectional"},
            )
        ]
    )
    result = await ex.extract([rct, unstructured])

    assert result[rct.pmid].design == "rct"
    assert result[rct.pmid].sample_size == 24
    assert result[rct.pmid].outcome_class == "physiological"
    assert result[rct.pmid].source == "llm"
    assert result[rct.pmid].prompt == "extract_metadata@v1"
    assert result[unstructured.pmid].design == "cross_sectional"
    assert len(provider.prompts) == 1


async def test_articles_without_abstract_skip_the_llm(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    ex, provider = extractor([])
    result = await ex.extract([articles[90000103]])
    assert result[90000103].design == "commentary"
    assert result[90000103].source == "publication_types"
    assert provider.prompts == []


async def test_batches_respect_batch_size(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    with_abstract = [articles[p] for p in (90000101, 90000102, 90000105)]
    ex, provider = extractor(
        [batch({"pmid": 90000101}, {"pmid": 90000102}), batch({"pmid": 90000105})], batch_size=2
    )
    await ex.extract(with_abstract)
    assert len(provider.prompts) == 2
    assert "90000105" in provider.prompts[1]


async def test_llm_failure_falls_back_to_publication_types(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    ex, _ = extractor([RateLimitedError("slow down")])
    result = await ex.extract([articles[90000101], articles[90000105]])
    assert result[90000101].design == "rct"
    assert result[90000105].design == "narrative_review"
    assert {m.source for m in result.values()} == {"publication_types"}


async def test_invalid_json_twice_falls_back(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    ex, provider = extractor(["not json", '{"articles": [{"pmid": "x"}]}'])
    result = await ex.extract([articles[90000102]])
    assert result[90000102].source == "publication_types"
    assert len(provider.prompts) == 2


async def test_pmids_missing_from_the_reply_keep_the_fallback(
    articles: dict[int, ParsedArticle], extractor: BuildExtractor
) -> None:
    ex, _ = extractor([batch({"pmid": 90000101, "sample_size": 24}, {"pmid": 12345})])
    result = await ex.extract([articles[90000101], articles[90000102]])
    assert result[90000101].sample_size == 24
    assert result[90000102].source == "publication_types"
    assert 12345 not in result


@pytest.mark.parametrize(
    ("pub_types", "include_fallback", "expected"),
    [
        (["Journal Article", "Review", "Meta-Analysis"], False, "meta_analysis"),
        (["Review"], False, None),
        (["Review"], True, "narrative_review"),
        (["Journal Article"], True, None),
        (["Letter", "Comment"], False, "commentary"),
    ],
)
def test_design_from_pub_types(
    pub_types: list[str], include_fallback: bool, expected: str | None
) -> None:
    assert design_from_pub_types(pub_types, include_fallback=include_fallback) == expected
