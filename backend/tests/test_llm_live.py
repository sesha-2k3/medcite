"""Live check that LLM_API_KEY works and every configured model ID exists.

    uv run pytest -m live tests/test_llm_live.py

Uses your real .env. Costs three tiny requests against your Groq free-plan limits.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.models.llm import ChatMessage, LLMRole, build_llm_client


class Pong(BaseModel):
    pong: bool


@pytest.mark.live
@pytest.mark.parametrize("role", list(LLMRole))
async def test_each_role_returns_valid_json(role: LLMRole) -> None:
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.skip("LLM_API_KEY not set")
    client = build_llm_client(settings)
    result = await client.complete_json(
        role,
        [ChatMessage("user", 'Reply with the JSON object {"pong": true} and nothing else.')],
        Pong,
        max_tokens=512,
    )
    assert result.pong is True
