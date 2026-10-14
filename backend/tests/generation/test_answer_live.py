"""The real generator and judge on synthetic passages (no database, no PubMed).

    uv run pytest -m live tests/generation/test_answer_live.py -s

Uses your Groq key: two to four generator and judge calls per test. On the free plan the
regeneration may wait for the per-minute token limit to reset.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.generation.factory import build_answer_service
from app.generation.gap import states_gap
from app.generation.verify import first_sentence
from app.models.llm import UsageMeter, build_llm_client
from tests.generation.builders import QUESTION, creatine_passages

pytestmark = pytest.mark.live


@pytest.fixture
def settings() -> Settings:
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.skip("LLM_API_KEY not set")
    return settings


async def test_gap_and_conflict_on_synthetic_evidence(settings: Settings) -> None:
    service = build_answer_service(settings, build_llm_client(settings))
    meter = UsageMeter()
    result = await service.answer(QUESTION, creatine_passages(), evidence_gap=True, meter=meter)
    answer, verification = result.answer, result.verification

    print(f"\n{answer.direct_answer}\n\n{answer.synthesis}\n")
    for item in verification.issues:
        print(f"{item.severity}: {item.kind} at {item.location}: {item.message}")
    for call in meter.calls:
        print(f"{call.role.value}: in {call.input_tokens}, out {call.output_tokens}")

    assert states_gap(first_sentence(answer.direct_answer), [])
    assert not any(i.kind == "unknown_citation" for i in verification.issues)
    cited = {c.marker for c in answer.citations}
    assert {"C1", "C3"} <= cited, "both directions of the conflicting evidence are cited"
