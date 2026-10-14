"""A scripted LLM provider shared by tests: replays canned replies and records prompts."""

from __future__ import annotations

from collections.abc import Sequence

from app.config import ReasoningEffort
from app.models.llm import ChatMessage, ProviderResult

Reply = str | Exception


class ScriptedProvider:
    def __init__(self, replies: list[Reply]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.systems: list[str] = []

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
        self.systems.append(messages[0].content if messages[0].role == "system" else "")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ProviderResult(text=reply, input_tokens=100, output_tokens=50)
