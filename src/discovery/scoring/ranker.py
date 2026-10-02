"""Composite score, bands, low-evidence guardrail, and weight sensitivity (P6.6-P6.9)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from discovery.config import ScoreBands, ScoringSettings
from discovery.scoring.dimensions import DIMENSIONS, DimensionScore

# Twelve scenarios: each of the six weights, plus and minus 0.05, then re-normalized.
SENSITIVITY_DELTA = 0.05


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """SC-09: weights that do not sum to 1 are scaled so they do. Non-positive totals fail."""
    cleaned = {key: max(0.0, float(weights[key])) for key in DIMENSIONS}
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError("scoring weights must include a positive weight")
    if math.isclose(total, 1.0, abs_tol=1e-6):
        return cleaned
    return {key: cleaned[key] / total for key in DIMENSIONS}


def weighted_sum(scores: Mapping[str, float], weights: Mapping[str, float]) -> float:
    weights = normalize_weights(weights)
    return round(sum(weights[key] * float(scores[key]) for key in DIMENSIONS), 2)


def band_for(composite: float, bands: ScoreBands) -> str:
    if composite >= bands.high:
        return "High"
    if composite >= bands.medium:
        return "Medium"
    return "Low"


def emerging_label(
    item_count: int, evidence_quality: float, settings: ScoringSettings
) -> str | None:
    """Why an area is low-evidence, or None when the evidence is adequate (SC-11)."""
    reasons = []
    if item_count < settings.low_evidence_min_items:
        reasons.append(f"{item_count} items (< {settings.low_evidence_min_items})")
    if evidence_quality < settings.low_evidence_min_quality:
        reasons.append(
            f"evidence quality {evidence_quality:.2f} (< {settings.low_evidence_min_quality:g})"
        )
    if not reasons:
        return None
    return "Emerging – low evidence: " + "; ".join(reasons)


def composite_explanation(
    scores: Mapping[str, float], weights: Mapping[str, float], total: float
) -> str:
    weights = normalize_weights(weights)
    parts = [f"{weights[key]:.2f}×{float(scores[key]):.2f}" for key in DIMENSIONS]
    return f"Composite {total:.2f} = " + " + ".join(parts) + "."


@dataclass
class AreaRanking:
    """One area after every dimension, the composite, and the guardrail are applied."""

    area_id: str
    name: str
    category: str
    item_count: int
    vague_items: int
    dimensions: dict[str, DimensionScore]
    composite: float
    composite_ai: float
    band: str
    low_evidence_reason: str | None
    weights: dict[str, float]
    rank: int = 0
    rank_ai: int = 0
    sensitivity: dict[str, Any] = field(default_factory=dict)

    @property
    def low_evidence(self) -> bool:
        return self.low_evidence_reason is not None

    def scores(self, *, ai: bool = False) -> dict[str, float]:
        out = {key: dim.score for key, dim in self.dimensions.items()}
        if ai:
            for key in ("product_leverage", "research_value"):
                out[key] = float(self.dimensions[key].detail.get("ai_score", out[key]))
        return out


def _order_key(area: AreaRanking, composite: float) -> tuple:
    """Adequate evidence first, then composite, then the SC-06 tie-break, then area id."""
    return (
        area.low_evidence,
        -composite,
        -area.dimensions["evidence_quality"].score,
        -area.item_count,
        area.area_id,
    )


def rank_areas(areas: Sequence[AreaRanking], *, ai: bool = False) -> list[AreaRanking]:
    """Low-evidence areas stay below every adequately evidenced area, whatever their score."""
    composite_of = (lambda area: area.composite_ai) if ai else (lambda area: area.composite)
    ordered = sorted(areas, key=lambda area: _order_key(area, composite_of(area)))
    for index, area in enumerate(ordered, start=1):
        if ai:
            area.rank_ai = index
        else:
            area.rank = index
    return ordered


def _top(areas: Sequence[AreaRanking], *, ai: bool = False) -> list[str]:
    ordered = rank_areas(areas, ai=ai)
    return [area.area_id for area in ordered[:3]]


def weight_sensitivity(
    areas: Sequence[AreaRanking], weights: Mapping[str, float]
) -> dict[str, Any]:
    """Whether the top 3 (order included) moves when each weight shifts by ±0.05 (P6.9)."""
    baseline = _top(areas)
    ai_top = _top(areas, ai=True)
    changes = []
    for key in DIMENSIONS:
        for delta in (SENSITIVITY_DELTA, -SENSITIVITY_DELTA):
            trial = dict(weights)
            trial[key] = min(1.0, max(0.0, float(trial[key]) + delta))
            trial = normalize_weights(trial)
            scratch = [
                AreaRanking(
                    area_id=area.area_id,
                    name=area.name,
                    category=area.category,
                    item_count=area.item_count,
                    vague_items=area.vague_items,
                    dimensions=area.dimensions,
                    composite=weighted_sum(area.scores(), trial),
                    composite_ai=area.composite_ai,
                    band=area.band,
                    low_evidence_reason=area.low_evidence_reason,
                    weights=trial,
                )
                for area in areas
            ]
            top = _top(scratch)
            if top != baseline:
                changes.append({"dimension": key, "delta": delta, "top3": top})
    return {
        "top3_stable": not changes,
        "baseline_top3": baseline,
        "baseline_top3_ai": ai_top,
        "overrides_change_top3": baseline != ai_top,
        "scenarios_checked": len(DIMENSIONS) * 2,
        "changes": changes,
    }


def apply_ranks(areas: list[AreaRanking], weights: Mapping[str, float]) -> dict[str, Any]:
    """Set rank, the AI-only rank, and the sensitivity summary on every area."""
    rank_areas(areas)
    rank_areas(areas, ai=True)
    summary = weight_sensitivity(areas, weights)
    for area in areas:
        area.sensitivity = summary
    return summary
