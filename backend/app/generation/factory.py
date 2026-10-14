"""Wiring for answer generation and verification."""

from __future__ import annotations

from app.config import Settings
from app.generation.context import ContextBudget
from app.generation.generate import Generator
from app.generation.service import AnswerService
from app.generation.verify import Judge, Verifier
from app.models.llm import LLMClient


def context_budget(settings: Settings) -> ContextBudget:
    return ContextBudget(
        max_tokens=settings.generation_context_tokens,
        passage_max_chars=settings.passage_max_chars,
        min_chunks=settings.context_min_chunks,
        max_chunks=settings.context_max_chunks,
    )


def build_answer_service(settings: Settings, llm: LLMClient) -> AnswerService:
    return AnswerService(
        generator=Generator(llm, max_tokens=settings.generation_max_tokens),
        verifier=Verifier(Judge(llm, max_tokens=settings.judge_max_tokens)),
        budget=context_budget(settings),
    )
