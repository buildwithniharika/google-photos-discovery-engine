"""Live re-rank from the six published dimension scores and the slider weights.

The composite matches the pipeline (`weighted_sum` + the same low-evidence ordering).
Weight changes do not write to the database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dashboard.labels import DIMENSIONS
from discovery.scoring.ranker import normalize_weights, weighted_sum


def live_rank(
    areas: Sequence[Mapping], weights: Mapping[str, float], *, high: float, medium: float
) -> list[dict]:
    """Return new dicts, best opportunity first. Low-evidence areas stay at the bottom."""
    normalized = normalize_weights(weights)
    ranked: list[dict] = []
    for area in areas:
        scores = {key: float(area[key]) for key in DIMENSIONS}
        composite = weighted_sum(scores, normalized)
        if composite >= high:
            band = "High"
        elif composite >= medium:
            band = "Medium"
        else:
            band = "Low"
        ranked.append(
            {
                **dict(area),
                "composite_live": composite,
                "band_live": band,
                "weights_live": normalized,
            }
        )
    ranked.sort(
        key=lambda area: (
            bool(area.get("low_evidence_flag")),
            -float(area["composite_live"]),
            -float(area["evidence_quality"]),
            -int(area.get("match_count") or 0),
            str(area["area_id"]),
        )
    )
    for index, area in enumerate(ranked, start=1):
        area["rank"] = index
    return ranked
