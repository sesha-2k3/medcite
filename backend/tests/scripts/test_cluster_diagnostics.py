from __future__ import annotations

from app.discovery.clusters import center
from scripts.cluster_diagnostics import (
    OLD_CUT,
    cut_into,
    distance_matrix,
    nearest_neighbour,
    pair_distances,
    threshold_cut,
)
from tests.discovery.builders import shared_direction_batch


def test_centering_spreads_distances() -> None:
    vectors = shared_direction_batch()
    raw = pair_distances(distance_matrix(vectors))
    centered = pair_distances(distance_matrix(center(vectors)))
    assert raw.max() < OLD_CUT
    assert centered.max() > 1.0


def test_pairs_and_nearest_neighbour_shapes() -> None:
    matrix = distance_matrix(shared_direction_batch(topics=2, per_topic=5))
    assert pair_distances(matrix).shape == (45,)
    assert nearest_neighbour(matrix).shape == (10,)
    assert nearest_neighbour(matrix).min() >= 0.0


def test_the_old_threshold_merges_a_shared_direction_batch() -> None:
    assert [len(c) for c in threshold_cut(shared_direction_batch(), OLD_CUT)] == [24]


def test_cut_into_returns_the_requested_count_largest_first() -> None:
    clusters = cut_into(center(shared_direction_batch()), 4)
    assert len(clusters) == 4
    assert sorted(i for c in clusters for i in c) == list(range(24))
