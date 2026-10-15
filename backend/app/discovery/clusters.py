"""Group articles into candidate themes by their embeddings (no model call; deterministic)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from sklearn.cluster import AgglomerativeClustering

MIN_THEME_SIZE = 2


def cluster_vectors(
    vectors: npt.NDArray[np.float32],
    *,
    distance: float,
    max_clusters: int,
    min_size: int = MIN_THEME_SIZE,
) -> list[list[int]]:
    """Row indices per cluster, largest first, members most representative first.

    Average-linkage agglomerative clustering on cosine distance, cut at ``distance``.
    Clusters smaller than ``min_size`` are dropped: a theme needs at least two articles.
    """
    count = len(vectors)
    if count < max(2, min_size):
        return []
    labels = AgglomerativeClustering(
        n_clusters=None, distance_threshold=distance, metric="cosine", linkage="average"
    ).fit_predict(vectors)
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(int(label), []).append(index)
    kept = [members for members in groups.values() if len(members) >= min_size]
    ordered = [_by_centrality(vectors, members) for members in kept]
    ordered.sort(key=lambda members: (-len(members), min(members)))
    return ordered[:max_clusters]


def _by_centrality(vectors: npt.NDArray[np.float32], members: list[int]) -> list[int]:
    centroid = vectors[members].mean(axis=0)
    similarity = vectors[members] @ centroid
    order = sorted(range(len(members)), key=lambda i: (-float(similarity[i]), members[i]))
    return [members[i] for i in order]
