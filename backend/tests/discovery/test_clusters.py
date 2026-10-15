from __future__ import annotations

import numpy as np

from app.discovery.clusters import cluster_vectors
from tests.discovery.builders import VECTORS, unit


def test_two_topics_form_two_clusters_most_central_first() -> None:
    vectors = np.stack(list(VECTORS.values()))
    assert cluster_vectors(vectors, distance=0.35, max_clusters=8) == [[0, 1, 2], [3, 4, 5]]


def test_outliers_are_left_out() -> None:
    vectors = np.stack([*VECTORS.values(), unit((9, 1.0))])
    clusters = cluster_vectors(vectors, distance=0.35, max_clusters=8)
    assert all(6 not in members for members in clusters)
    assert len(clusters) == 2


def test_largest_cluster_first_and_capped() -> None:
    vectors = np.stack([*VECTORS.values(), unit((0, 1.0), (4, 0.05))])
    clusters = cluster_vectors(vectors, distance=0.35, max_clusters=1)
    assert len(clusters) == 1
    assert sorted(clusters[0]) == [0, 1, 2, 6]


def test_too_few_articles_make_no_themes() -> None:
    assert cluster_vectors(np.stack([unit((0, 1.0))]), distance=0.35, max_clusters=8) == []
    far_apart = np.stack([unit((0, 1.0)), unit((1, 1.0))])
    assert cluster_vectors(far_apart, distance=0.35, max_clusters=8) == []
