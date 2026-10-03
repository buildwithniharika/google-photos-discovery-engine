"""Local text embeddings for Stage B and clustering.

The hosted LLM is used for generation only. Vectors are L2-normalized float32, stored as raw
bytes in the `embeddings` table (architecture Section 13.2). Stage B embeds `clean_text`
under the model name; clustering embeds the extracted problem under
`{model}{INSIGHT_SUFFIX}`, so both fit the table's (item_id, model) key.
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

INSIGHT_SUFFIX = "#insight"


def insight_text(
    problem_statement: str | None, trying_to_find: str | None, breakdown_point: str | None
) -> str:
    """P5.1: the extracted problem, not the user's wording, so clusters group by problem
    rather than by writing style or rating."""
    parts = []
    if problem_statement and problem_statement.strip():
        parts.append(problem_statement.strip().rstrip(".") + ".")
    if trying_to_find and trying_to_find.strip():
        parts.append(f"Looking for: {trying_to_find.strip().rstrip('.')}.")
    if breakdown_point and breakdown_point != "not_stated":
        parts.append(f"Breakdown: {breakdown_point.replace('_', ' ')}.")
    return " ".join(parts)


class Encoder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return one L2-normalized row per text. Empty input returns shape (0, 0)."""


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.size == 0:
        return matrix.reshape(0, matrix.shape[-1] if matrix.ndim == 2 else 0)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    return matrix / norms


def pack_vector(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


class SentenceEncoder:
    """`sentence-transformers` wrapper. The model is loaded on the first call."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: object | None = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        model = self._load()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32)

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed. Run `uv sync` and retry."
                ) from exc
            logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
            logging.getLogger("transformers").setLevel(logging.WARNING)
            log.info("Loading embedding model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model
