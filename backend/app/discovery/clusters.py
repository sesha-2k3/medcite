"""Group articles into candidate themes by their embeddings (no model call; deterministic)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from sklearn.cluster import AgglomerativeClustering

MIN_THEME_SIZE = 2


def center(vectors: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """Subtract the batch mean, then rescale rows to unit length."""
    shifted = vectors - vectors.mean(axis=0)
    norms = np.linalg.norm(shifted, axis=1, keepdims=True)
    return (shifted / np.where(norms == 0, 1.0, norms)).astype(np.float32)


def target_groups(count: int, *, per_theme: int, max_groups: int, min_size: int) -> int:
    """About one group per ``per_theme`` articles, between 2 and ``max_groups``; a batch too
    small to split into two themes stays one group.
    """
    if count < 2 * min_size or max_groups < 2:
        return 1
    return max(2, min(max_groups, round(count / per_theme)))


def cluster_vectors(
    vectors: npt.NDArray[np.float32],
    *,
    per_theme: int,
    max_clusters: int,
    min_size: int = MIN_THEME_SIZE,
) -> list[list[int]]:
    """Row indices per cluster, largest first, members most representative first.

    MedCPT abstract vectors share one dominant direction (the batch mean has norm near 0.9),
    so raw cosine distances crowd into 0.1 to 0.4 and no distance cut separates topics.
    Centering removes that direction; cutting the average-linkage tree into a target count,
    rather than at a distance, keeps the result stable across broad and narrow topics.
    Clusters smaller than ``min_size`` are dropped: a theme needs at least two articles.
    """
    count = len(vectors)
    if count < max(2, min_size):
        return []
    centered = center(vectors)
    groups = target_groups(count, per_theme=per_theme, max_groups=max_clusters, min_size=min_size)
    if groups == 1:
        labels = np.zeros(count, dtype=int)
    else:
        labels = AgglomerativeClustering(
            n_clusters=groups, metric="cosine", linkage="average"
        ).fit_predict(centered)
    members: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        members.setdefault(int(label), []).append(index)
    kept = [m for m in members.values() if len(m) >= min_size]
    ordered = [_by_centrality(centered, m) for m in kept]
    ordered.sort(key=lambda m: (-len(m), min(m)))
    return ordered[:max_clusters]


def _by_centrality(vectors: npt.NDArray[np.float32], members: list[int]) -> list[int]:
    centroid = vectors[members].mean(axis=0)
    similarity = vectors[members] @ centroid
    order = sorted(range(len(members)), key=lambda i: (-float(similarity[i]), members[i]))
    return [members[i] for i in order]
