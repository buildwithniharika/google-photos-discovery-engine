"""Display labels loaded from config. No database access."""

from __future__ import annotations

from functools import lru_cache

import yaml

from discovery.config import PROJECT_ROOT

SOURCE_LABELS = {
    "play_store": "Play Store",
    "app_store": "App Store",
    "google_sheet": "Reddit Scraped Reviews",
    "google_community": "Google Community",
}

PLATFORMS = (
    "Android",
    "iOS",
    "Reddit",
    "Google Community",
    "YouTube",
    "Web Forum",
)

DIMENSIONS = (
    "frequency",
    "severity",
    "strategic_fit",
    "evidence_quality",
    "product_leverage",
    "research_value",
)

DIMENSION_LABELS = {
    "frequency": "Frequency",
    "severity": "Severity",
    "strategic_fit": "Strategic fit",
    "evidence_quality": "Evidence quality",
    "product_leverage": "Product leverage",
    "research_value": "Research value",
}

SCOPE_LABELS = {
    "all": "All",
    "vague": "Vague memory",
    "general": "General retrieval",
}

LOW_CONFIDENCE = 0.6
PAGE_SIZE = 25


def titleize(value: str | None) -> str:
    if not value:
        return "—"
    return str(value).replace("_", " ").strip().capitalize()


def source_label(value: str | None) -> str:
    if not value:
        return "—"
    return SOURCE_LABELS.get(value, titleize(value))


@lru_cache(maxsize=1)
def _taxonomy() -> dict:
    path = PROJECT_ROOT / "config" / "taxonomy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _weights_file() -> dict:
    path = PROJECT_ROOT / "config" / "scoring_weights.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def category_labels() -> dict[str, str]:
    return dict(_taxonomy()["categories"])


def content_types() -> tuple[str, ...]:
    return tuple(_taxonomy()["content_types"])


def retrieval_types() -> tuple[str, ...]:
    return tuple(_taxonomy()["retrieval_types"])


def breakdown_order() -> tuple[str, ...]:
    return tuple(_taxonomy()["breakdown_points"])


def cue_types() -> tuple[str, ...]:
    return tuple(_taxonomy()["cue_types"])


def forgotten_details() -> tuple[str, ...]:
    return tuple(_taxonomy()["forgotten_details"])


def attempt_types() -> tuple[str, ...]:
    return tuple(_taxonomy()["attempt_types"])


def default_weights() -> dict[str, float]:
    return {key: float(value) for key, value in _weights_file()["weights"].items()}


def score_bands() -> tuple[float, float]:
    bands = _weights_file()["bands"]
    return float(bands["high"]), float(bands["medium"])
