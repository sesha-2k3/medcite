from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.config import ReasoningEffort, Settings
from app.errors import LLMOutputError, UpstreamError
from app.models.llm import (
    ChatMessage,
    LLMClient,
    LLMRole,
    OpenAICompatibleProvider,
    ProviderResult,
    UsageMeter,
)


class Answer(BaseModel):
    verdict: str
    n: int


class FakeProvider:
    """Returns scripted replies and records each call."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "model": model,
                "messages": list(messages),
                "json_mode": json_mode,
                "reasoning_effort": reasoning_effort,
            }
        )
        return ProviderResult(text=self.replies.pop(0), input_tokens=1000, output_tokens=500)


MSGS = [ChatMessage("user", "Extract.")]


async def test_roles_route_to_configured_models(make_settings: Callable[..., Settings]) -> None:
    fake = FakeProvider(["a", "b", "c"])
    client = LLMClient(make_settings(), fake)
    for role in LLMRole:
        await client.complete(role, MSGS)
    assert [c["model"] for c in fake.calls] == [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
    ]


async def test_complete_json_valid_first_try(make_settings: Callable[..., Settings]) -> None:
    fake = FakeProvider(['```json\n{"verdict": "gap", "n": 3}\n```'])
    result = await LLMClient(make_settings(), fake).complete_json(LLMRole.PLANNER, MSGS, Answer)
    assert result == Answer(verdict="gap", n=3)
    assert fake.calls[0]["json_mode"] is True


async def test_complete_json_repairs_once(make_settings: Callable[..., Settings]) -> None:
    fake = FakeProvider(['{"verdict": "gap"}', '{"verdict": "gap", "n": 2}'])
    meter = UsageMeter()
    result = await LLMClient(make_settings(), fake).complete_json(
        LLMRole.PLANNER, MSGS, Answer, meter=meter
    )
    assert result.n == 2
    repair_history = fake.calls[1]["messages"]
    assert isinstance(repair_history, list)
    assert repair_history[-1].role == "user"
    assert "did not match" in repair_history[-1].content
    assert len(meter.calls) == 2


async def test_complete_json_fails_after_one_repair(make_settings: Callable[..., Settings]) -> None:
    fake = FakeProvider(["not json", "still not json"])
    with pytest.raises(LLMOutputError):
        await LLMClient(make_settings(), fake).complete_json(LLMRole.JUDGE, MSGS, Answer)
    assert len(fake.calls) == 2


async def test_cost_uses_configured_prices(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(llm_prices={"openai/gpt-oss-120b": (0.15, 0.60)})
    meter = UsageMeter()
    client = LLMClient(settings, FakeProvider(["x", "y"]))
    await client.complete(LLMRole.GENERATOR, MSGS, meter=meter)  # 1000 in, 500 out
    await client.complete(LLMRole.PLANNER, MSGS, meter=meter)  # unpriced -> free
    assert meter.cost_usd == Decimal("0.00045")


def test_groq_reasoning_params_per_model_family(make_settings: Callable[..., Settings]) -> None:
    p = OpenAICompatibleProvider(make_settings(llm_provider="groq"))
    assert p._extra_body("openai/gpt-oss-20b", "none") == {
        "reasoning_effort": "low",
        "include_reasoning": False,
    }
    assert p._extra_body("qwen/qwen3.8-27b", "low") == {
        "reasoning_effort": "low",
        "reasoning_format": "hidden",
    }
    assert p._extra_body("some/other-model", "low") == {}


def test_non_groq_host_gets_no_vendor_params(make_settings: Callable[..., Settings]) -> None:
    p = OpenAICompatibleProvider(make_settings(llm_provider="openai_compatible"))
    assert p._extra_body("openai/gpt-oss-20b", "low") == {}


async def test_missing_api_key_is_a_typed_error(make_settings: Callable[..., Settings]) -> None:
    p = OpenAICompatibleProvider(make_settings(llm_api_key=None))
    with pytest.raises(UpstreamError, match="LLM_API_KEY"):
        await p.chat(
            model="m",
            messages=MSGS,
            max_tokens=10,
            temperature=0,
            json_mode=False,
            reasoning_effort="low",
        )
