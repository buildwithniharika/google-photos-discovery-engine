"""Deterministic score dimensions (architecture Section 10.1, tasks P6.1-P6.4).

Frequency, severity, strategic fit, and evidence quality are pure functions of the
items. The same items always produce the same scores. Product leverage and research
value are rubric judgments and live in `rubric.py`.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

VAGUE = "vague_memory_retrieval"
# Thumbs-up (Play), upvotes and vote sums (Sheet, App Store), "same question" (Community).
ENGAGEMENT_KEYS = ("same_question", "thumbs_up", "upvotes", "vote_sum")
DIMENSIONS = (
    "frequency",
    "severity",
    "strategic_fit",
    "evidence_quality",
    "product_leverage",
    "research_value",
)

# Severity weights from Section 10.1. Sources with no ratings drop the third term.
_INTENSITY_W = 0.5
_STAKES_W = 0.3
_RATING_W = 0.2
_PLATFORM_SATURATION = 4
_SAMPLE_SATURATION = 50


@dataclass(frozen=True)
class DimensionScore:
    score: float
    explanation: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredItem:
    """One in-scope feedback item, with the fields the four count-based dimensions need."""

    item_id: str
    source: str
    platform: str
    retrieval_type: str | None
    vague_memory_relevance: float | None
    frustration_intensity: int | None
    high_stakes: bool | None
    evidence_strength: int | None
    rating: int | None
    engagement: dict[str, Any]
    date: datetime | None
    is_spam: bool = False
    problem_statement: str = ""
    trying_to_find: str = ""
    breakdown_point: str | None = None
    evidence_quote: str | None = None
    quote_grounded: bool | None = None


def clamp_score(value: float) -> float:
    """Every dimension is on a 1-5 scale, rounded to two decimals."""
    return round(min(5.0, max(1.0, value)), 2)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def window_start(as_of: datetime, days: int | None) -> datetime | None:
    """First instant still inside the analysis window, or None when every date is in scope."""
    if days is None:
        return None
    return _as_utc(as_of) - timedelta(days=days)


def in_window(item: ScoredItem, start: datetime | None) -> bool:
    """Undated items stay in: there is no date to call them old. Spam is never in scope."""
    if item.is_spam:
        return False
    if start is None or item.date is None:
        return True
    return _as_utc(item.date) >= start


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(value: float) -> str:
    return f"{value:.0%}"


# --- frequency ----------------------------------------------------------------


def _engagement_signal(item: ScoredItem, cap: int) -> float:
    """Log-scaled engagement in [0, 1]. Each channel is capped before the log (SC-05)."""
    ceiling = math.log1p(cap) * len(ENGAGEMENT_KEYS)
    if ceiling <= 0:
        return 0.0
    total = 0.0
    for key in ENGAGEMENT_KEYS:
        raw = item.engagement.get(key) or 0
        if isinstance(raw, int | float) and raw > 0:
            total += math.log1p(min(float(raw), cap))
    return total / ceiling


def engagement_factor(items: Sequence[ScoredItem], *, cap: int, alpha: float) -> float:
    """1.0 when nobody engaged, at most 1 + alpha when every channel is at the cap."""
    if not items or alpha <= 0:
        return 1.0
    mean = sum(_engagement_signal(it, cap) for it in items) / len(items)
    return 1.0 + alpha * mean


def cap_by_day(items: Sequence[ScoredItem], fraction: float) -> tuple[list[ScoredItem], int]:
    """Drop vague items beyond one day's cap (SC-08).

    A day may contribute at most `fraction` of the area's vague items, and at least one.
    Undated items are kept: they are not a single-day burst. Which items survive a cap is
    decided by item id, so a re-run keeps the same ones.
    """
    grouped: dict[datetime | None, list[ScoredItem]] = defaultdict(list)
    for item in items:
        day = _as_utc(item.date).date() if item.date is not None else None
        grouped[day].append(item)
    limit = max(1, math.floor(fraction * len(items))) if items else 0
    kept: list[ScoredItem] = []
    dropped = 0
    for day in sorted(grouped, key=lambda d: (d is None, d or datetime.min.date())):
        group = sorted(grouped[day], key=lambda it: it.item_id)
        if day is None:
            kept.extend(group)
            continue
        kept.extend(group[:limit])
        dropped += max(0, len(group) - limit)
    return kept, dropped


def _source_shares(capped: Sequence[ScoredItem], corpus: Sequence[ScoredItem]) -> dict[str, float]:
    """Per-source share of corpus vague items. A source the area misses contributes 0."""
    totals = Counter(it.source for it in corpus)
    in_area = Counter(it.source for it in capped)
    return {source: in_area[source] / total for source, total in sorted(totals.items()) if total}


def adjusted_share(
    area_items: Sequence[ScoredItem],
    corpus_vague: Sequence[ScoredItem],
    *,
    day_cap: float,
    engagement_cap: int,
    engagement_alpha: float,
    raw_weight: float,
) -> tuple[float, dict[str, Any]]:
    """Blended vague share, times the engagement factor. 0 when the corpus has no vague items."""
    vague = [it for it in area_items if it.retrieval_type == VAGUE]
    capped, dropped = cap_by_day(vague, day_cap)
    corpus_n = len(corpus_vague)
    raw = len(capped) / corpus_n if corpus_n else 0.0
    shares = _source_shares(capped, corpus_vague)
    balanced = sum(shares.values()) / len(shares) if shares else 0.0
    blended = raw_weight * raw + (1 - raw_weight) * balanced
    factor = engagement_factor(vague, cap=engagement_cap, alpha=engagement_alpha)
    adjusted = blended * factor
    detail = {
        "raw_share": round(raw, 4),
        "vague_items_counted": len(capped),
        "vague_items_before_day_cap": len(vague),
        "day_cap_dropped": dropped,
        "corpus_vague_items": corpus_n,
        "source_shares": {k: round(v, 4) for k, v in shares.items()},
        "source_balanced_share": round(balanced, 4),
        "blended_share": round(blended, 4),
        "engagement_factor": round(factor, 4),
        "adjusted_share": round(adjusted, 6),
    }
    return adjusted, detail


def fixed_threshold_score(share: float, thresholds: Sequence[float]) -> float:
    """1 below the first cutoff, 2 below the second, ..., 5 at or above the last."""
    for score, cutoff in enumerate(thresholds, start=1):
        if share < cutoff:
            return float(score)
    return 5.0


def quantile_scores(values: Sequence[float]) -> list[float]:
    """Map values to 1-5 by mid-rank. Ties receive the same score; the ends are 1 and 5."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [3.0]
    scores = []
    for value in values:
        below = sum(other < value for other in values)
        ties = sum(other == value for other in values)
        percentile = (below + (ties - 1) / 2) / (n - 1)
        scores.append(round(1 + 4 * percentile, 2))
    return scores


def score_frequency(
    shares: Sequence[float], *, min_areas: int, thresholds: Sequence[float]
) -> tuple[list[float], str]:
    """Quantiles once there are enough areas to cut into fifths; fixed cutoffs otherwise (SC-02)."""
    if len(shares) < min_areas:
        return [fixed_threshold_score(s, thresholds) for s in shares], "fixed_threshold"
    return quantile_scores(shares), "quantile"


def frequency_explanation(score: float, detail: Mapping[str, Any], mapping: str) -> str:
    counted = detail["vague_items_counted"]
    corpus = detail["corpus_vague_items"]
    text = (
        f"Frequency {score:.2f} = {mapping} of adjusted share {detail['adjusted_share']:.4f} "
        f"(raw {detail['raw_share']:.0%} = {counted}/{corpus} vague items, "
        f"source-balanced {detail['source_balanced_share']:.0%}, "
        f"engagement ×{detail['engagement_factor']:.2f})"
    )
    dropped = detail["day_cap_dropped"]
    if dropped:
        text += f". Day cap dropped {dropped} same-day items"
    return text + "."


# --- severity, strategic fit, evidence quality --------------------------------


def score_severity(items: Sequence[ScoredItem]) -> DimensionScore:
    """0.5 intensity + 0.3 high-stakes + 0.2 low ratings. No ratings: re-weight the first two."""
    n = len(items)
    frustration = [it.frustration_intensity for it in items if it.frustration_intensity]
    intensity = _mean([float(v) for v in frustration])
    used_intensity = 1.0 if intensity is None else intensity
    stakes = sum(1 for it in items if it.high_stakes) / n if n else 0.0
    rated = [it.rating for it in items if it.rating is not None]
    low = sum(1 for r in rated if r <= 2) / len(rated) if rated else None

    if low is None:
        denom = _INTENSITY_W + _STAKES_W
        raw = (_INTENSITY_W / denom) * used_intensity + (_STAKES_W / denom) * (5 * stakes)
        rating_text = "no ratings, so intensity and high-stakes were re-weighted to sum to 1"
    else:
        raw = _INTENSITY_W * used_intensity + _STAKES_W * (5 * stakes) + _RATING_W * (5 * low)
        rating_text = f"{_pct(low)} ≤2-star ({sum(1 for r in rated if r <= 2)}/{len(rated)} rated)"
    score = clamp_score(raw)
    missing = " (frustration not stated, counted as 1)" if intensity is None else ""
    explanation = (
        f"Severity {score:.2f} = intensity {used_intensity:.2f}{missing}, "
        f"{_pct(stakes)} high-stakes, {rating_text}."
    )
    return DimensionScore(
        score,
        explanation,
        {
            "intensity": None if intensity is None else round(intensity, 4),
            "intensity_used": round(used_intensity, 4),
            "high_stakes_share": round(stakes, 4),
            "low_rating_share": None if low is None else round(low, 4),
            "rated_items": len(rated),
            "ratings_reweighted": low is None,
            "raw": round(raw, 4),
        },
    )


def score_strategic_fit(items: Sequence[ScoredItem]) -> DimensionScore:
    """5 × mean vague-memory relevance × the area's vague share, clamped to 1-5."""
    n = len(items)
    relevance = [it.vague_memory_relevance for it in items if it.vague_memory_relevance is not None]
    mean_rel = _mean([float(v) for v in relevance]) or 0.0
    vague = sum(1 for it in items if it.retrieval_type == VAGUE)
    share = vague / n if n else 0.0
    raw = 5 * mean_rel * share
    score = clamp_score(raw)
    clamped = "" if 1 <= raw <= 5 else f" Raw {raw:.2f}, clamped to {score:.2f}."
    explanation = (
        f"Strategic fit {score:.2f} = 5 × mean vague relevance {mean_rel:.2f} "
        f"× vague share {_pct(share)} ({vague}/{n}).{clamped}"
    )
    return DimensionScore(
        score,
        explanation,
        {
            "mean_vague_relevance": round(mean_rel, 4),
            "vague_items": vague,
            "items": n,
            "vague_share": round(share, 4),
            "raw": round(raw, 4),
            "clamped": score != round(raw, 2),
        },
    )


def score_evidence_quality(items: Sequence[ScoredItem]) -> DimensionScore:
    """Quote strength, platform spread (saturated at 4), and sample size (saturated at 50)."""
    n = len(items)
    strengths = [it.evidence_strength for it in items if it.evidence_strength]
    mean_strength = _mean([float(v) for v in strengths]) or 1.0
    platforms = sorted({it.platform for it in items if it.platform})
    platform_term = 5 * min(len(platforms), _PLATFORM_SATURATION) / _PLATFORM_SATURATION
    sample_term = 5 * min(1.0, math.log(n) / math.log(_SAMPLE_SATURATION)) if n >= 2 else 0.0
    raw = 0.4 * mean_strength + 0.3 * platform_term + 0.3 * sample_term
    score = clamp_score(raw)
    diversity = (
        f"{len(platforms)} platform" if len(platforms) == 1 else f"{len(platforms)} platforms"
    )
    named = ", ".join(platforms) if platforms else "none"
    explanation = (
        f"Evidence quality {score:.2f} = mean strength {mean_strength:.2f}, "
        f"{diversity} ({named}), sample {n} items."
    )
    if len(platforms) <= 1:
        explanation += " One platform, so diversity is low."
    return DimensionScore(
        score,
        explanation,
        {
            "mean_evidence_strength": round(mean_strength, 4),
            "platforms": platforms,
            "platform_count": len(platforms),
            "items": n,
            "platform_term": round(platform_term, 4),
            "sample_term": round(sample_term, 4),
            "raw": round(raw, 4),
        },
    )
