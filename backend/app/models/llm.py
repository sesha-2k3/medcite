"""The only module that talks to an LLM provider.

Callers ask for a *role* (planner, generator, judge), never a model or a vendor:

    client = build_llm_client(settings)
    resp = await client.complete(LLMRole.GENERATOR, [ChatMessage("user", "...")])
    plan, calls = await client.complete_json(LLMRole.PLANNER, messages, Plan)

Swapping provider or model is configuration (``LLM_PROVIDER``, ``LLM_BASE_URL``,
``LLM_MODEL_*``). The default deployment is Groq through its OpenAI-compatible endpoint.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from app.config import ReasoningEffort, Settings
from app.errors import LLMOutputError, RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)


class LLMRole(StrEnum):
    PLANNER = "planner"  # routing, PICO, query building, metadata extraction (small model)
    GENERATOR = "generator"  # answer synthesis (strong model)
    JUDGE = "judge"  # verification (different model family from the generator)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """What a provider adapter returns: text plus token accounting."""

    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One completed call, with everything we log and bill."""

    role: LLMRole
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal


@dataclass(slots=True)
class UsageMeter:
    """Accumulates calls for one user request (feeds query_logs and usage_daily)."""

    calls: list[LLMResponse] = field(default_factory=list)

    def add(self, response: LLMResponse) -> None:
        self.calls.append(response)

    @property
    def cost_usd(self) -> Decimal:
        return sum((c.cost_usd for c in self.calls), Decimal(0))


class LLMProvider(Protocol):
    """Adapter contract. Implementations translate errors into app.errors types."""

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderResult: ...


class OpenAICompatibleProvider:
    """Chat Completions over any OpenAI-compatible endpoint (Groq by default).

    The SDK retries 429/5xx/timeouts with exponential backoff and honors ``retry-after``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None  # created on first use so the app boots without a key

    def _get_client(self) -> Any:
        if self._client is None:
            if self._settings.llm_api_key is None:
                raise UpstreamError("LLM_API_KEY is not configured")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._settings.llm_api_key.get_secret_value(),
                base_url=self._settings.llm_base_url,
                timeout=self._settings.llm_timeout_s,
                max_retries=self._settings.llm_max_retries,
            )
        return self._client

    def _extra_body(self, model: str, effort: ReasoningEffort) -> dict[str, Any]:
        """Groq-specific reasoning controls; empty for other hosts and models."""
        if self._settings.llm_provider != "groq":
            return {}
        if model.startswith("openai/gpt-oss"):
            # GPT-OSS supports low/medium/high only and cannot disable reasoning.
            return {
                "reasoning_effort": "low" if effort == "none" else effort,
                "include_reasoning": False,
            }
        if model.startswith("qwen/"):
            return {"reasoning_effort": effort, "reasoning_format": "hidden"}
        return {}

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
        import openai

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        extra = self._extra_body(model, reasoning_effort)
        if extra:
            kwargs["extra_body"] = extra

        try:
            completion = await self._get_client().chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise RateLimitedError(
                f"LLM provider rate limit reached for {model}",
                retry_after_s=_retry_after(exc),
            ) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise UpstreamError(f"LLM provider unreachable: {exc}") from exc
        except openai.APIStatusError as exc:
            raise UpstreamError(f"LLM provider error {exc.status_code}: {exc.message}") from exc

        text = completion.choices[0].message.content or ""
        usage = completion.usage
        return ProviderResult(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def _retry_after(exc: Any) -> float | None:
    try:
        return float(exc.response.headers["retry-after"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


_DEFAULT_TEMPERATURE = {LLMRole.PLANNER: 0.2, LLMRole.GENERATOR: 0.3, LLMRole.JUDGE: 0.0}
_DEFAULT_MAX_TOKENS = {LLMRole.PLANNER: 2048, LLMRole.GENERATOR: 4096, LLMRole.JUDGE: 2048}


class LLMClient:
    """Role-based facade over a provider: model choice, cost, logging, JSON validation."""

    def __init__(self, settings: Settings, provider: LLMProvider) -> None:
        self._settings = settings
        self._provider = provider

    def model_for(self, role: LLMRole) -> str:
        return {
            LLMRole.PLANNER: self._settings.llm_model_planner,
            LLMRole.GENERATOR: self._settings.llm_model_generator,
            LLMRole.JUDGE: self._settings.llm_model_judge,
        }[role]

    def _reasoning_for(self, role: LLMRole) -> ReasoningEffort:
        return {
            LLMRole.PLANNER: self._settings.llm_reasoning_planner,
            LLMRole.GENERATOR: self._settings.llm_reasoning_generator,
            LLMRole.JUDGE: self._settings.llm_reasoning_judge,
        }[role]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        price_in, price_out = self._settings.llm_prices.get(model, (0.0, 0.0))
        usd = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        return Decimal(str(round(usd, 6)))

    async def complete(
        self,
        role: LLMRole,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        meter: UsageMeter | None = None,
    ) -> LLMResponse:
        model = self.model_for(role)
        started = time.perf_counter()
        result = await self._provider.chat(
            model=model,
            messages=messages,
            max_tokens=max_tokens or _DEFAULT_MAX_TOKENS[role],
            temperature=_DEFAULT_TEMPERATURE[role] if temperature is None else temperature,
            json_mode=json_mode,
            reasoning_effort=self._reasoning_for(role),
        )
        response = LLMResponse(
            role=role,
            model=model,
            text=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=self.cost(model, result.input_tokens, result.output_tokens),
        )
        logger.info(
            "llm_call",
            extra={
                "llm_role": role.value,
                "llm_model": model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
                "cost_usd": str(response.cost_usd),
            },
        )
        if meter is not None:
            meter.add(response)
        return response

    async def complete_json[T: BaseModel](
        self,
        role: LLMRole,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        meter: UsageMeter | None = None,
    ) -> T:
        """Return a validated ``schema`` instance; one repair attempt, then LLMOutputError."""
        history = list(messages)
        first = await self.complete(
            role, history, max_tokens=max_tokens, json_mode=True, meter=meter
        )
        try:
            return _parse(first.text, schema)
        except (ValueError, ValidationError) as exc:
            logger.warning("llm_json_invalid", extra={"llm_role": role.value, "error": str(exc)})
            history += [
                ChatMessage("assistant", first.text),
                ChatMessage(
                    "user",
                    "Your previous reply did not match the required JSON schema.\n"
                    f"Problem: {_short(exc)}\n"
                    "Reply with only the corrected JSON object and nothing else.",
                ),
            ]

        second = await self.complete(
            role, history, max_tokens=max_tokens, json_mode=True, meter=meter
        )
        try:
            return _parse(second.text, schema)
        except (ValueError, ValidationError) as exc:
            raise LLMOutputError(
                f"{role.value} output failed validation after one repair: {_short(exc)}"
            ) from exc


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    """Strip code fences / surrounding prose, then validate against the schema."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    data = json.loads(cleaned[start : end + 1])
    return schema.model_validate(data)


def _short(exc: Exception, limit: int = 500) -> str:
    return str(exc).replace("\n", " ")[:limit]


def build_llm_client(settings: Settings) -> LLMClient:
    """Provider selection lives here and nowhere else."""
    match settings.llm_provider:
        case "groq" | "openai_compatible":
            return LLMClient(settings, OpenAICompatibleProvider(settings))
