"""Synthetic evidence passages (PMIDs at or above 90,000,000) and stored LLM outputs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.db.queries.chunks import ChunkView
from app.ingest.metadata import Design, OutcomeClass, StudyMetadata
from app.retrieval.evidence import grade_study
from app.retrieval.hybrid_search import HybridHit
from app.retrieval.rerank import ScoredChunk
from app.retrieval.retriever import EvidencePassage

QUESTION = "Does creatine improve grip strength in older adults?"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"


def stored(name: str, **changes: Any) -> str:
    """A stored LLM reply as JSON text, with top-level fields replaced."""
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    data.update(changes)
    return json.dumps(data)


def stored_data(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def passage(
    chunk_id: int,
    article_id: int,
    content: str,
    *,
    section: str = "Abstract-RESULTS",
    chunk_index: int = 0,
    design: Design = "rct",
    sample_size: int | None = 40,
    year: int | None = 2024,
    tier: int | None = 2,
    relevance: float = 5.0,
    outcome: str | None = None,
    outcome_class: OutcomeClass = "unknown",
) -> EvidencePassage:
    meta = StudyMetadata(
        design=design,
        sample_size=sample_size,
        primary_outcome=outcome,
        outcome_class=outcome_class,
    )
    chunk = ChunkView(
        chunk_id=chunk_id,
        article_id=article_id,
        pmid=90_000_000 + article_id,
        section=section,
        chunk_index=chunk_index,
        content=content,
        title=f"Synthetic study {article_id}",
        journal=None,
        pub_date=date(year, 1, 1) if year else None,
        pub_types=(),
        publication_status=None,
        study_meta=meta,
    )
    return EvidencePassage(
        scored=ScoredChunk(
            chunk=chunk,
            hit=HybridHit(chunk_id=chunk_id, rrf_score=0.01, dense_rank=1, sparse_rank=None),
            relevance=relevance,
        ),
        grade=grade_study(meta),
        tier=tier,
    )


def creatine_passages() -> list[EvidencePassage]:
    """Rank order: trial results, cohort results, trial conclusions, indirect review.

    The trial and the cohort disagree. After grouping by article the markers are
    C1 trial RESULTS, C2 trial CONCLUSIONS, C3 cohort RESULTS, C4 review RESULTS.
    """
    return [
        passage(
            11,
            1,
            "Grip strength increased by 2.1 kg with creatine and by 0.4 kg with placebo "
            "(p = 0.03) after 12 weeks.",
            chunk_index=1,
            relevance=8.0,
        ),
        passage(
            21,
            2,
            "Creatine use was not associated with grip strength (difference 0.2 kg; "
            "95% CI -0.5 to 0.9).",
            design="cohort",
            sample_size=310,
            year=2023,
            relevance=6.0,
        ),
        passage(
            12,
            1,
            "Creatine supplementation improved grip strength in older adults.",
            section="Abstract-CONCLUSIONS",
            chunk_index=2,
            relevance=4.0,
        ),
        passage(
            31,
            3,
            "Across 9 trials in adults of all ages, creatine increased upper-body strength.",
            design="systematic_review",
            sample_size=None,
            year=2021,
            tier=3,
            relevance=2.0,
        ),
    ]
