"""Runs the real MedCPT encoders; skips unless `make models` has downloaded them."""

from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.models.embeddings import MedCPTEmbedder, load_embedder

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def embedder() -> MedCPTEmbedder:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        return load_embedder(settings, local_files_only=True)
    except OSError as exc:
        pytest.skip(f"MedCPT weights not downloaded ({exc}); run: make models")


def test_article_vectors_are_768_dim_unit_length(embedder: MedCPTEmbedder) -> None:
    vectors = embedder.embed_articles(
        [("Resistance training in older adults", "Strength improved after 12 weeks.")] * 3
    )
    assert vectors.shape == (3, 768)
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)


def test_query_vectors_are_768_dim_unit_length(embedder: MedCPTEmbedder) -> None:
    vectors = embedder.embed_queries(["biceps hypertrophy", "metformin in older adults"])
    assert vectors.shape == (2, 768)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)


def test_relevant_article_scores_higher_than_unrelated(embedder: MedCPTEmbedder) -> None:
    [query] = embedder.embed_queries(["does curl training grow the biceps"])
    relevant, unrelated = embedder.embed_articles(
        [
            ("Elbow flexor hypertrophy after curl training", "Biceps thickness increased."),
            ("Metformin and kidney function", "eGFR declined in older patients."),
        ]
    )
    assert float(query @ relevant) > float(query @ unrelated)


def test_batching_matches_single_pass(embedder: MedCPTEmbedder) -> None:
    pairs = [(f"Title {i}", f"Text about topic {i}.") for i in range(20)]
    batched = embedder.embed_articles(pairs)
    single = np.vstack([embedder.embed_articles([pair]) for pair in pairs[:3]])
    np.testing.assert_allclose(batched[:3], single, atol=1e-4)


def test_empty_input(embedder: MedCPTEmbedder) -> None:
    assert embedder.embed_articles([]).shape == (0, 768)
