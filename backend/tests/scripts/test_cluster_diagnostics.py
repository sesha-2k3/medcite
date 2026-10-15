from __future__ import annotations

import numpy as np
import numpy.typing as npt

from scripts.cluster_diagnostics import (
    center,
    cut_into,
    distance_matrix,
    nearest_neighbour,
    pair_distances,
    target_groups,
)


def _batch(seed: int = 0) -> npt.NDArray[np.float32]:
    """Two topics on top of one large shared direction, like real MedCPT abstracts."""
    rng = np.random.default_rng(seed)
    shared = np.zeros(16)
    shared[0] = 1.0
    rows = []
    for topic in (1, 2):
        for _ in range(5):
            row = 4.0 * shared + rng.normal(0, 0.05, 16)
            row[topic] += 1.0
            rows.append(row / np.linalg.norm(row))
    return np.stack(rows).astype(np.float32)


def test_centering_spreads_distances() -> None:
    vectors = _batch()
    raw = pair_distances(distance_matrix(vectors))
    centered = pair_distances(distance_matrix(center(vectors)))
    assert raw.max() < 0.35
    assert centered.max() > 1.0


def test_centered_rows_are_unit_length() -> None:
    assert np.allclose(np.linalg.norm(center(_batch()), axis=1), 1.0, atol=1e-5)


def test_pairs_and_nearest_neighbour_shapes() -> None:
    matrix = distance_matrix(_batch())
    assert pair_distances(matrix).shape == (45,)
    assert nearest_neighbour(matrix).shape == (10,)
    assert nearest_neighbour(matrix).min() >= 0.0


def test_target_cut_separates_topics_after_centering() -> None:
    clusters = cut_into(center(_batch()), 2)
    assert sorted(sorted(c) for c in clusters) == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]


def test_target_groups_is_clamped() -> None:
    assert target_groups(3, 6, 8) == 2
    assert target_groups(30, 6, 8) == 5
    assert target_groups(200, 6, 8) == 8
