from __future__ import annotations

import numpy as np

from app.discovery.clusters import center, cluster_vectors, target_groups
from tests.discovery.builders import VECTORS, shared_direction_batch, unit


def test_two_topics_form_two_clusters_most_central_first() -> None:
    vectors = np.stack(list(VECTORS.values()))
    clusters = cluster_vectors(vectors, per_theme=3, max_clusters=8)
    assert clusters == [[0, 1, 2], [3, 4, 5]]


def test_topics_separate_despite_a_shared_direction() -> None:
    vectors = shared_direction_batch()
    clusters = cluster_vectors(vectors, per_theme=6, max_clusters=8)
    assert len(clusters) == 4
    assert all(len({i % 4 for i in members}) == 1 for members in clusters)


def test_centering_removes_the_shared_direction() -> None:
    vectors = shared_direction_batch()
    assert float(np.linalg.norm(vectors.mean(axis=0))) > 0.9
    assert float(np.linalg.norm(center(vectors).mean(axis=0))) < 0.1
    assert np.allclose(np.linalg.norm(center(vectors), axis=1), 1.0, atol=1e-5)


def test_singleton_clusters_are_left_out() -> None:
    vectors = np.stack([*VECTORS.values(), unit((9, 1.0))])
    clusters = cluster_vectors(vectors, per_theme=2, max_clusters=3)
    assert sorted(sorted(c) for c in clusters) == [[0, 1, 2], [3, 4, 5]]


def test_largest_cluster_first_and_capped() -> None:
    vectors = shared_direction_batch(topics=3, per_topic=4)
    clusters = cluster_vectors(vectors, per_theme=4, max_clusters=2)
    assert len(clusters) <= 2
    assert [len(c) for c in clusters] == sorted((len(c) for c in clusters), reverse=True)


def test_a_small_batch_is_one_theme() -> None:
    vectors = np.stack([unit((0, 1.0)), unit((0, 1.0), (1, 0.2)), unit((1, 1.0))])
    clusters = cluster_vectors(vectors, per_theme=6, max_clusters=8)
    assert [sorted(c) for c in clusters] == [[0, 1, 2]]


def test_a_single_article_makes_no_themes() -> None:
    assert cluster_vectors(np.stack([unit((0, 1.0))]), per_theme=6, max_clusters=8) == []


def test_target_groups() -> None:
    assert target_groups(3, per_theme=6, max_groups=8, min_size=2) == 1
    assert target_groups(6, per_theme=6, max_groups=8, min_size=2) == 2
    assert target_groups(30, per_theme=6, max_groups=8, min_size=2) == 5
    assert target_groups(200, per_theme=6, max_groups=8, min_size=2) == 8
    assert target_groups(30, per_theme=6, max_groups=1, min_size=2) == 1
