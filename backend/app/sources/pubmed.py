"""NCBI E-utilities client for PubMed search (ESearch) and record retrieval (EFetch)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.config import Settings
from app.errors import AppError, RateLimitedError, UpstreamError
from app.sources.cache import Cache, cache_key
from app.sources.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

EFETCH_BATCH_SIZE = 200
ESEARCH_MAX_RESULTS = 10_000
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

Sort = Literal["relevance", "pub_date"]
DateType = Literal["edat", "pdat", "mdat"]


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive window; dates as YYYY, YYYY/MM or YYYY/MM/DD."""

    datetype: DateType
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    count: int
    pmids: list[int]
    query_translation: str | None
    warnings: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PubMedConfig:
    base_url: str
    tool: str
    email: str
    api_key: str | None
    cache_ttl_s: int

    @classmethod
    def from_settings(cls, settings: Settings) -> PubMedConfig:
        return cls(
            base_url=settings.ncbi_base_url.rstrip("/"),
            tool=settings.ncbi_tool,
            email=settings.ncbi_email,
            api_key=settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None,
            cache_ttl_s=settings.ncbi_cache_ttl_s,
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 4
    initial_wait_s: float = 0.5
    max_wait_s: float = 8.0


class _TransientResponseError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, _TransientResponseError | httpx.TransportError)


class PubMedClient:
    """ESearch and EFetch against PubMed, sharing one rate limiter, cache and retry policy."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        limiter: RateLimiter,
        cache: Cache,
        config: PubMedConfig,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._http = http
        self._limiter = limiter
        self._cache = cache
        self._config = config
        self._retry = retry or RetryPolicy()

    async def esearch(
        self,
        term: str,
        *,
        retmax: int = 100,
        retstart: int = 0,
        sort: Sort = "relevance",
        dates: DateRange | None = None,
    ) -> SearchResult:
        """Return matching PMIDs. PubMed serves at most 10,000 results per query."""
        if not term.strip():
            raise ValueError("term must not be empty")
        if retmax < 0 or retstart < 0 or retstart + retmax > ESEARCH_MAX_RESULTS:
            raise ValueError(f"retstart + retmax must be within 0..{ESEARCH_MAX_RESULTS}")
        params: dict[str, object] = {
            "db": "pubmed",
            "term": " ".join(term.split()),
            "retmax": retmax,
            "retstart": retstart,
            "sort": sort,
            "retmode": "json",
        }
        if dates is not None:
            params |= {"datetype": dates.datetype, "mindate": dates.start, "maxdate": dates.end}
        return await self._cached("esearch.fcgi", params, parse_esearch)

    async def efetch(self, pmids: Sequence[int]) -> list[str]:
        """Return PubMed XML, one document per batch of up to 200 PMIDs, in request order."""
        unique = list(dict.fromkeys(pmids))
        batches = [
            unique[i : i + EFETCH_BATCH_SIZE] for i in range(0, len(unique), EFETCH_BATCH_SIZE)
        ]
        return list(await asyncio.gather(*(self._efetch_batch(batch) for batch in batches)))

    async def _efetch_batch(self, pmids: Sequence[int]) -> str:
        params: dict[str, object] = {
            "db": "pubmed",
            "id": ",".join(str(p) for p in pmids),
            "retmode": "xml",
        }
        return await self._cached("efetch.fcgi", params, check_efetch)

    async def _cached[T](
        self, endpoint: str, params: Mapping[str, object], parse: Callable[[str], T]
    ) -> T:
        """Serve from cache, else fetch; only bodies that parse are cached."""
        key = cache_key(f"pubmed:{endpoint}", params)
        cached = await self._cache.get(key)
        if cached is not None:
            return parse(cached)
        body = await self._get(endpoint, params)
        result = parse(body)
        await self._cache.set(key, body, self._config.cache_ttl_s)
        return result

    async def _get(self, endpoint: str, params: Mapping[str, object]) -> str:
        url = f"{self._config.base_url}/{endpoint}"
        query = {**params, **self._identity()}
        retrying = AsyncRetrying(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(self._retry.attempts),
            wait=wait_exponential_jitter(
                multiplier=self._retry.initial_wait_s, max=self._retry.max_wait_s
            ),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    return await self._send(endpoint, url, query)
        except _TransientResponseError as exc:
            raise _final_error(exc.response) from exc
        except httpx.TransportError as exc:
            raise UpstreamError(f"NCBI unreachable: {exc!r}") from exc
        raise AssertionError("retry loop exited without a result")

    async def _send(self, endpoint: str, url: str, query: Mapping[str, object]) -> str:
        await self._limiter.acquire()
        started = time.perf_counter()
        response = await self._http.get(url, params=_stringify(query))
        logger.info(
            "ncbi_request",
            extra={
                "endpoint": endpoint,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        if response.status_code in TRANSIENT_STATUS:
            raise _TransientResponseError(response)
        if response.is_error:
            raise UpstreamError(f"NCBI {endpoint} returned {response.status_code}")
        return response.text

    def _identity(self) -> dict[str, str]:
        identity = {"tool": self._config.tool, "email": self._config.email}
        if self._config.api_key:
            identity["api_key"] = self._config.api_key
        return identity


def parse_esearch(body: str) -> SearchResult:
    try:
        result: dict[str, Any] = json.loads(body)["esearchresult"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpstreamError("Malformed ESearch response") from exc
    if "ERROR" in result:
        raise UpstreamError(f"ESearch rejected the query: {result['ERROR']}")
    return SearchResult(
        count=int(result.get("count", 0)),
        pmids=[int(pmid) for pmid in result.get("idlist", [])],
        query_translation=result.get("querytranslation"),
        warnings=_messages(result.get("errorlist")) + _messages(result.get("warninglist")),
        not_found=[
            *(result.get("errorlist") or {}).get("phrasesnotfound", []),
            *(result.get("warninglist") or {}).get("quotedphrasesnotfound", []),
        ],
    )


def check_efetch(body: str) -> str:
    if "<PubmedArticleSet" not in body:
        raise UpstreamError("Malformed EFetch response")
    return body


def _messages(section: Mapping[str, list[str]] | None) -> list[str]:
    if not section:
        return []
    return [f"{kind}: {item}" for kind, items in section.items() for item in items]


def _final_error(response: httpx.Response) -> AppError:
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        return RateLimitedError(
            "NCBI rate limit reached",
            retry_after_s=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    return UpstreamError(f"NCBI returned {response.status_code} after retries")


def _stringify(query: Mapping[str, object]) -> dict[str, str]:
    return {name: str(value) for name, value in query.items()}
