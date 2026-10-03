"""Clustering of extracted retrieval problems (architecture Section 9.1).

P5.2: UMAP reduction, then HDBSCAN. Cluster 0 is the largest; -1 is noise.
P5.4: same-category clusters with close centroids are grouped into one opportunity area.
P5.9: a cluster close to a cluster of the previous run keeps that cluster's area, so area
ids and PM curation survive re-runs.

Centroids are computed in the original embedding space (L2-normalized mean), never in the
UMAP space, so they stay comparable across runs.
"""

from __future__ import annotations

import itertools
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from discovery.ai.embeddings import normalize_rows
from discovery.config import ClusteringSettings

log = logging.getLogger(__name__)

Reducer = Callable[[np.ndarray], np.ndarray]
NOISE = -1


def umap_reducer(settings: ClusteringSettings, n_neighbors: int | None = None) -> Reducer:
    def reduce(vectors: np.ndarray) -> np.ndarray:
        import umap  # numba compiles on first import; keep it out of CLI startup

        n = len(vectors)
        model = umap.UMAP(
            n_components=min(settings.umap.n_components, n - 2),
            n_neighbors=min(n_neighbors or settings.umap.n_neighbors, n - 1),
            metric=settings.umap.metric,
            min_dist=settings.umap.min_dist,
            random_state=settings.random_state,
        )
        return np.asarray(model.fit_transform(vectors), dtype=np.float32)

    return reduce


def hdbscan_labels(
    points: np.ndarray, min_cluster_size: int, min_samples: int | None, method: str
) -> np.ndarray:
    from sklearn.cluster import HDBSCAN

    if len(points) < min_cluster_size:
        return np.full(len(points), NOISE, dtype=int)
    model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method=method,
        copy=True,  # without UMAP the input is the embedding matrix, reused for centroids
    )
    return relabel_by_size(model.fit_predict(points))


def relabel_by_size(labels: np.ndarray) -> np.ndarray:
    """Renumber clusters so 0 is the largest (ties: the one whose first member comes first)."""
    labels = np.asarray(labels, dtype=int)
    ids = [c for c in np.unique(labels) if c != NOISE]
    first = {c: int(np.argmax(labels == c)) for c in ids}
    order = sorted(ids, key=lambda c: (-int((labels == c).sum()), first[c]))
    mapping = {old: new for new, old in enumerate(order)}
    return np.array([mapping.get(int(c), NOISE) for c in labels], dtype=int)


def cluster_labels(
    vectors: np.ndarray, settings: ClusteringSettings, *, reducer: Reducer | None = None
) -> np.ndarray:
    """One label per row of `vectors` (cluster number, or -1 for noise)."""
    n = len(vectors)
    hdb = settings.hdbscan
    if n < hdb.min_cluster_size:
        return np.full(n, NOISE, dtype=int)
    if reducer is None:
        # UMAP's spectral layout needs a few more points than output dimensions.
        reducer = umap_reducer(settings) if n > settings.umap.n_components + 2 else _identity
    points = reducer(vectors)
    return hdbscan_labels(
        points, hdb.min_cluster_size, hdb.min_samples, hdb.cluster_selection_method
    )


def _identity(vectors: np.ndarray) -> np.ndarray:
    return vectors


def centroid(vectors: np.ndarray) -> np.ndarray:
    return normalize_rows(np.asarray(vectors, dtype=np.float32).mean(axis=0))[0]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def coherence(vectors: np.ndarray, labels: np.ndarray) -> float | None:
    """Mean cosine of members to their cluster centroid, averaged over clusters."""
    values = []
    for c in sorted(set(labels.tolist()) - {NOISE}):
        members = vectors[labels == c]
        values.append(float((members @ centroid(members)).mean()))
    return float(np.mean(values)) if values else None


def diverse_top(
    candidates: Sequence[tuple[str, str, float]],
    k: int,
    *,
    reject: Callable[[str, list[str]], bool] | None = None,
) -> list[str]:
    """Pick up to `k` ids from (id, source, score): the best of each source first, then
    the rest by score. `reject(id, picked)` skips near-duplicates. Returned best first."""
    ordered = sorted(candidates, key=lambda c: (-c[2], c[0]))
    rank = {cid: n for n, (cid, _, _) in enumerate(ordered)}
    picked: list[str] = []
    chosen: set[str] = set()
    sources: set[str] = set()

    def take(cid: str) -> None:
        if reject is not None and reject(cid, picked):
            return
        picked.append(cid)
        chosen.add(cid)

    for cid, source, _ in ordered:
        if len(picked) >= k:
            break
        if source not in sources:
            sources.add(source)
            take(cid)
    for cid, _, _ in ordered:
        if len(picked) >= k:
            break
        if cid not in chosen:
            take(cid)
    return sorted(picked, key=rank.__getitem__)


# --- P5.4: clusters -> opportunity areas -------------------------------------


@dataclass(frozen=True)
class ClusterNode:
    key: str
    category: str
    centroid: np.ndarray
    size: int
    area_id: str | None = None  # fixed by a previous run or by PM curation


@dataclass
class AreaGroup:
    keys: list[str]
    area_id: str | None
    category: str
    weighted: np.ndarray = field(repr=False)  # size-weighted sum of member centroids
    size: int

    @property
    def centroid(self) -> np.ndarray:
        return normalize_rows(self.weighted)[0]


def majority_category(nodes: Sequence[ClusterNode]) -> str:
    weights: Counter[str] = Counter()
    for node in nodes:
        weights[node.category] += node.size
    top = max(weights.values())
    for node in sorted(nodes, key=lambda n: -n.size):
        if weights[node.category] == top:
            return node.category
    raise AssertionError("unreachable")


def group_clusters(nodes: Sequence[ClusterNode], threshold: float) -> list[AreaGroup]:
    """Greedy centroid linkage. Nodes with the same fixed `area_id` start as one group; two
    fixed groups are never merged (a split the PM made stays split). Otherwise the closest
    pair of same-category groups at or above `threshold` is merged until none is left."""
    groups: list[AreaGroup] = []
    by_area: dict[str, list[ClusterNode]] = {}
    for node in nodes:
        if node.area_id is None:
            groups.append(
                AreaGroup([node.key], None, node.category, node.centroid * node.size, node.size)
            )
        else:
            by_area.setdefault(node.area_id, []).append(node)
    for area_id, members in by_area.items():
        groups.append(
            AreaGroup(
                [m.key for m in members],
                area_id,
                majority_category(members),
                np.sum([m.centroid * m.size for m in members], axis=0),
                sum(m.size for m in members),
            )
        )
    while True:
        best: tuple[float, int, int] | None = None
        for i, j in itertools.combinations(range(len(groups)), 2):
            a, b = groups[i], groups[j]
            if (a.area_id and b.area_id) or a.category != b.category:
                continue
            sim = cosine(a.weighted, b.weighted)
            if sim >= threshold and (best is None or sim > best[0]):
                best = (sim, i, j)
        if best is None:
            break
        _, i, j = best
        a, b = groups[i], groups.pop(j)
        a.keys.extend(b.keys)
        a.area_id = a.area_id or b.area_id
        a.weighted = a.weighted + b.weighted
        a.size += b.size
    return sorted(groups, key=lambda g: (-g.size, g.keys[0]))


# --- P5.9: match clusters to the previous run --------------------------------


@dataclass(frozen=True)
class PriorCluster:
    cluster_id: str
    area_id: str
    centroid: np.ndarray


def match_prior(
    new: dict[str, np.ndarray], prior: Sequence[PriorCluster], threshold: float
) -> dict[str, tuple[PriorCluster, float]]:
    """New cluster key -> the most similar prior cluster at or above `threshold`."""
    matches: dict[str, tuple[PriorCluster, float]] = {}
    for key, vec in new.items():
        best: tuple[PriorCluster, float] | None = None
        for p in prior:
            if p.centroid.shape != vec.shape:
                continue
            sim = cosine(vec, p.centroid)
            if sim >= threshold and (best is None or sim > best[1]):
                best = (p, sim)
        if best is not None:
            matches[key] = best
    return matches


# --- tuning (P5.2) -----------------------------------------------------------


@dataclass(frozen=True)
class SweepResult:
    n_neighbors: int
    min_cluster_size: int
    min_samples: int | None
    method: str
    clusters: int
    noise_share: float
    largest_share: float
    coherence: float | None
    sizes: tuple[int, ...]


DEFAULT_GRID = {
    "n_neighbors": (10, 15, 30),
    "min_cluster_size": (6, 8, 10, 12, 15),
    "min_samples": (None, 3, 5),
    "method": ("eom", "leaf"),
}


def sweep(
    vectors: np.ndarray,
    settings: ClusteringSettings,
    grid: dict[str, Iterable] | None = None,
    *,
    reducer_for: Callable[[int], Reducer] | None = None,
) -> list[SweepResult]:
    """Cluster with every combination in `grid`. UMAP runs once per n_neighbors."""
    grid = {**DEFAULT_GRID, **(grid or {})}
    reducer_for = reducer_for or (lambda nn: umap_reducer(settings, nn))
    n = len(vectors)
    results: list[SweepResult] = []
    for nn in grid["n_neighbors"]:
        points = reducer_for(nn)(vectors)
        for mcs, ms, method in itertools.product(
            grid["min_cluster_size"], grid["min_samples"], grid["method"]
        ):
            labels = hdbscan_labels(points, mcs, ms, method)
            sizes = tuple(sorted(Counter(labels[labels != NOISE].tolist()).values(), reverse=True))
            results.append(
                SweepResult(
                    n_neighbors=nn,
                    min_cluster_size=mcs,
                    min_samples=ms,
                    method=method,
                    clusters=len(sizes),
                    noise_share=float((labels == NOISE).mean()) if n else 0.0,
                    largest_share=sizes[0] / n if sizes else 0.0,
                    coherence=coherence(vectors, labels),
                    sizes=sizes,
                )
            )
    return results


def render_sweep(results: Sequence[SweepResult], current: ClusteringSettings) -> str:
    lines = [
        "# Clustering sweep",
        "",
        "Coherence: mean cosine of members to their cluster centroid (embedding space).",
        "Look for several clusters of 10+ items, noise under ~25%, and no cluster holding "
        "most of the corpus.",
        "",
        "| n_neighbors | min_cluster_size | min_samples | method | clusters | noise | largest "
        "| coherence | sizes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    hdb = current.hdbscan
    for r in results:
        is_current = (
            r.n_neighbors == current.umap.n_neighbors
            and r.min_cluster_size == hdb.min_cluster_size
            and r.min_samples == hdb.min_samples
            and r.method == hdb.cluster_selection_method
        )
        coh = "-" if r.coherence is None else f"{r.coherence:.3f}"
        sizes = ", ".join(str(s) for s in r.sizes[:12]) + (" ..." if len(r.sizes) > 12 else "")
        lines.append(
            f"| {r.n_neighbors}{' (current)' if is_current else ''} | {r.min_cluster_size} | "
            f"{r.min_samples if r.min_samples is not None else '-'} | {r.method} | {r.clusters} | "
            f"{r.noise_share:.0%} | {r.largest_share:.0%} | {coh} | {sizes} |"
        )
    lines.append("")
    return "\n".join(lines)
