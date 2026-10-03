"""Stage B semantic filter (architecture Section 7).

Keep an item when its best cosine similarity to a positive seed exemplar exceeds its
best similarity to a negative exemplar by more than τ. A fixed-seed sample of the
rejections is sent on to Stage C so missed relevant items can be estimated.
"""

from __future__ import annotations

import random

import numpy as np

from discovery.ai.embeddings import Encoder, normalize_rows


def similarity_margin(item: np.ndarray, positive: np.ndarray, negative: np.ndarray) -> float:
    """max cosine(item, positive) − max cosine(item, negative). Vectors are normalized here."""
    if positive.size == 0 or negative.size == 0:
        raise ValueError("Stage B needs both positive and negative seed exemplars")
    item_row = normalize_rows(item)[0]
    pos = float(np.max(normalize_rows(positive) @ item_row))
    neg = float(np.max(normalize_rows(negative) @ item_row))
    return pos - neg


def keep_margin(margin: float, threshold: float) -> bool:
    """Architecture rule: keep when the margin is strictly above τ."""
    return margin > threshold


def audit_ids(rejected: list[str], rate: float, *, seed: int = 0) -> set[str]:
    """Deterministic sample of Stage B rejections. The same seed redraws the same sample."""
    if rate <= 0 or not rejected:
        return set()
    count = int(round(len(rejected) * rate))
    if count <= 0:
        return set()
    count = min(count, len(rejected))
    rng = random.Random(seed)
    return set(rng.sample(sorted(rejected), count))


def margins_for(
    encoder: Encoder, texts: list[str], positive: list[str], negative: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """One margin per text, plus the normalized item vectors. Exemplars are embedded once."""
    if not texts:
        empty = np.zeros((0, 0), dtype=np.float32)
        return np.zeros((0,), dtype=np.float32), empty
    item_vecs = normalize_rows(encoder.embed(texts))
    pos = normalize_rows(encoder.embed(positive))
    neg = normalize_rows(encoder.embed(negative))
    if pos.size == 0 or neg.size == 0:
        raise ValueError("Stage B needs both positive and negative seed exemplars")
    pos_max = np.max(item_vecs @ pos.T, axis=1)
    neg_max = np.max(item_vecs @ neg.T, axis=1)
    return (pos_max - neg_max).astype(np.float32), item_vecs
