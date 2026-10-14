from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest

from app.errors import RateLimitedError, UpstreamError
from app.sources.pubmed import DateRange, PubMedClient, PubMedConfig, RetryPolicy
from tests.sources.fakes import CountingLimiter, MemoryCache, efetch_xml, fixture

Handler = Callable[[httpx.Request], httpx.Response]
Step = httpx.Response | Exception


@dataclass
class Harness:
    client: PubMedClient
    limiter: CountingLimiter
    cache: MemoryCache
    requests: list[httpx.Request]


BuildHarness = Callable[..., Harness]


@pytest.fixture
async def build() -> AsyncIterator[BuildHarness]:
    opened: list[httpx.AsyncClient] = []

    def _build(handler: Handler, *, api_key: str | None = "test-key", attempts: int = 3) -> Harness:
        requests: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
        opened.append(http)
        limiter, cache = CountingLimiter(), MemoryCache()
        config = PubMedConfig(
            base_url="https://eutils.test/entrez/eutils",
            tool="medcite-test",
            email="dev@example.test",
            api_key=api_key,
            cache_ttl_s=60,
        )
        retry = RetryPolicy(attempts=attempts, initial_wait_s=0, max_wait_s=0)
        return Harness(PubMedClient(http, limiter, cache, config, retry), limiter, cache, requests)

    yield _build
    for http in opened:
        await http.aclose()


def respond(body: str, status: int = 200) -> Handler:
    return lambda _request: httpx.Response(status, text=body)


def sequence(*steps: Step) -> Handler:
    remaining: Iterator[Step] = iter(steps)

    def handler(_request: httpx.Request) -> httpx.Response:
        step = next(remaining)
        if isinstance(step, Exception):
            raise step
        return step

    return handler


def efetch_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=efetch_xml(request.url.params["id"].split(",")))


async def test_esearch_parses_ids_count_and_translation(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    result = await h.client.esearch("biceps hypertrophy", retmax=3)
    assert result.count == 1287
    assert result.pmids == [90000003, 90000002, 90000001]
    assert result.query_translation is not None
    assert "[MeSH Terms]" in result.query_translation
    assert result.warnings == []


async def test_every_request_identifies_the_caller(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    await h.client.esearch("biceps")
    params = h.requests[0].url.params
    assert params["tool"] == "medcite-test"
    assert params["email"] == "dev@example.test"
    assert params["api_key"] == "test-key"
    assert params["db"] == "pubmed"
    assert params["retmode"] == "json"
    assert h.requests[0].url.path == "/entrez/eutils/esearch.fcgi"


async def test_api_key_omitted_when_not_configured(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")), api_key=None)
    await h.client.esearch("biceps")
    assert "api_key" not in h.requests[0].url.params


async def test_esearch_sends_sort_paging_and_date_window(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    await h.client.esearch(
        "diabetes",
        retmax=50,
        retstart=100,
        sort="pub_date",
        dates=DateRange("edat", "2026/04/01", "3000"),
    )
    params = h.requests[0].url.params
    assert (params["retmax"], params["retstart"], params["sort"]) == ("50", "100", "pub_date")
    assert (params["datetype"], params["mindate"], params["maxdate"]) == (
        "edat",
        "2026/04/01",
        "3000",
    )


async def test_esearch_reports_phrases_not_found(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_no_results.json")))
    result = await h.client.esearch("xyzzyplugh")
    assert result.pmids == []
    assert "phrasesnotfound: xyzzyplugh" in result.warnings
    assert "outputmessages: No items found." in result.warnings
    assert result.not_found == ["xyzzyplugh"]


async def test_esearch_error_payload_raises_and_is_not_cached(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_error.json")))
    with pytest.raises(UpstreamError, match="Invalid query syntax"):
        await h.client.esearch("((broken")
    assert h.cache.store == {}


@pytest.mark.parametrize(("retmax", "retstart"), [(-1, 0), (0, -1), (10_000, 1)])
async def test_esearch_rejects_out_of_range_paging(
    build: BuildHarness, retmax: int, retstart: int
) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    with pytest.raises(ValueError, match="retmax"):
        await h.client.esearch("biceps", retmax=retmax, retstart=retstart)
    assert h.requests == []


async def test_esearch_rejects_blank_term(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    with pytest.raises(ValueError, match="term"):
        await h.client.esearch("   ")


async def test_efetch_returns_the_xml_body(build: BuildHarness) -> None:
    h = build(respond(fixture("efetch_two.xml")))
    [document] = await h.client.efetch([90000001, 90000002])
    assert '<PMID Version="1">90000002</PMID>' in document
    assert h.requests[0].url.params["retmode"] == "xml"


async def test_efetch_batches_200_ids_dedupes_and_keeps_order(build: BuildHarness) -> None:
    h = build(efetch_handler)
    pmids = [*list(range(90000001, 90000451)), 90000001, 90000002]
    documents = await h.client.efetch(pmids)

    sent = [r.url.params["id"].split(",") for r in h.requests]
    assert sorted(len(batch) for batch in sent) == [50, 200, 200]
    assert len(documents) == 3
    assert documents[0].count("<PubmedArticle>") == 200
    assert '<PMID Version="1">90000001</PMID>' in documents[0]
    assert '<PMID Version="1">90000450</PMID>' in documents[2]


async def test_efetch_with_no_ids_sends_nothing(build: BuildHarness) -> None:
    h = build(efetch_handler)
    assert await h.client.efetch([]) == []
    assert h.requests == []


async def test_efetch_rejects_non_pubmed_body(build: BuildHarness) -> None:
    h = build(respond("<html>Service unavailable</html>"))
    with pytest.raises(UpstreamError, match="EFetch"):
        await h.client.efetch([90000001])


async def test_retries_transient_status_then_succeeds(build: BuildHarness) -> None:
    h = build(
        sequence(
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, text=fixture("esearch_basic.json")),
        )
    )
    result = await h.client.esearch("biceps")
    assert result.count == 1287
    assert len(h.requests) == 3
    assert h.limiter.calls == 3


async def test_retries_timeouts_then_succeeds(build: BuildHarness) -> None:
    h = build(
        sequence(
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            httpx.Response(200, text=fixture("esearch_basic.json")),
        )
    )
    assert (await h.client.esearch("biceps")).count == 1287


async def test_exhausted_429_raises_rate_limited(build: BuildHarness) -> None:
    h = build(respond("", status=429), attempts=2)
    with pytest.raises(RateLimitedError) as info:
        await h.client.esearch("biceps")
    assert len(h.requests) == 2
    assert info.value.retry_after_s is None


async def test_exhausted_5xx_raises_upstream_error(build: BuildHarness) -> None:
    h = build(respond("", status=502), attempts=2)
    with pytest.raises(UpstreamError, match="502"):
        await h.client.esearch("biceps")


async def test_exhausted_timeouts_raise_upstream_error(build: BuildHarness) -> None:
    h = build(sequence(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")), attempts=2)
    with pytest.raises(UpstreamError, match="unreachable"):
        await h.client.esearch("biceps")


async def test_client_errors_are_not_retried(build: BuildHarness) -> None:
    h = build(respond("bad request", status=400))
    with pytest.raises(UpstreamError, match="400"):
        await h.client.esearch("biceps")
    assert len(h.requests) == 1


async def test_identical_search_is_served_from_cache(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    first = await h.client.esearch("biceps  hypertrophy")
    second = await h.client.esearch(" biceps hypertrophy ")
    assert first == second
    assert len(h.requests) == 1
    assert list(h.cache.ttls.values()) == [60]


async def test_cache_key_excludes_credentials(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    await h.client.esearch("biceps")
    [key] = h.cache.store
    assert "test-key" not in key
    assert key.startswith("pubmed:esearch.fcgi:")


async def test_different_parameters_miss_the_cache(build: BuildHarness) -> None:
    h = build(respond(fixture("esearch_basic.json")))
    await h.client.esearch("biceps", sort="relevance")
    await h.client.esearch("biceps", sort="pub_date")
    assert len(h.requests) == 2
