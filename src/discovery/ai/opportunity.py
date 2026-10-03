"""Deterministic opportunity-area outputs (architecture Section 9.2).

P5.5: aggregates per area (sources, content types, cues, forgotten details, attempts,
breakdown points, plus the inputs Phase 6 scoring needs).
P5.6: representative quotes: strong evidence, close to the area centroid, diverse across
sources, no near-duplicates.

Only `clean_text`-derived quotes are used (insights.evidence_quote is a verbatim span of
the redacted text), so redacted details never reappear.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from discovery.ai.clustering import diverse_top

TOP_CUES = 10
EXAMPLES_PER_ENTRY = 3


@dataclass(frozen=True)
class EvidenceItem:
    """One insight with the item and relevance fields synthesis and scoring need."""

    item_id: str
    source: str
    platform: str
    source_url: str
    retrieval_type: str | None
    vague_memory_relevance: float | None
    problem_statement: str
    trying_to_find: str
    content_type: str | None
    remembered_cues: list[dict[str, Any]] = field(default_factory=list)
    forgotten_details: list[str] = field(default_factory=list)
    search_attempts: list[dict[str, Any]] = field(default_factory=list)
    breakdown_point: str | None = None
    outcome: str | None = None
    emotion: str | None = None
    frustration_intensity: int | None = None
    primary_category: str | None = None
    high_stakes: bool | None = None
    evidence_quote: str | None = None
    evidence_strength: int | None = None
    quote_grounded: bool | None = None
    useful_for_discovery: bool | None = None
    rating: int | None = None
    engagement: dict[str, int] = field(default_factory=dict)
    date: datetime | None = None


_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_LEADING = re.compile(r"^(?:my|the|a|an|our|some|all|his|her|their)\s+")


def normalize_cue(text: str) -> str:
    """'My  "Goa trip"!' -> 'goa trip', so the same cue in different wording counts once."""
    out = re.sub(r"\s+", " ", (text or "").translate(_PUNCT).lower())
    previous = None
    while previous != out:
        previous = out
        out = _LEADING.sub("", out.strip().strip("\"'.,;:!?()[] "))
    return out


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _ordered(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def aggregates(items: Sequence[EvidenceItem]) -> dict[str, Any]:
    """Counts are per item: an item that names two `time_range` cues counts once."""
    n = len(items)
    retrieval = Counter(it.retrieval_type or "unknown" for it in items)
    cue_types: Counter[str] = Counter()
    cue_counts: Counter[str] = Counter()
    cue_examples: dict[str, list[str]] = defaultdict(list)
    forgotten: Counter[str] = Counter()
    attempt_types: Counter[str] = Counter()
    attempt_examples: dict[str, list[str]] = defaultdict(list)
    engagement: Counter[str] = Counter()
    for it in items:
        cue_types.update({c.get("cue_type") for c in it.remembered_cues if c.get("cue_type")})
        seen: set[str] = set()
        for cue in it.remembered_cues:
            raw = (cue.get("cue") or "").strip()
            key = normalize_cue(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            cue_counts[key] += 1
            if raw not in cue_examples[key] and len(cue_examples[key]) < EXAMPLES_PER_ENTRY:
                cue_examples[key].append(raw)
        forgotten.update(set(it.forgotten_details))
        kinds = {a.get("attempt_type") for a in it.search_attempts} - {None, "not_stated"}
        attempt_types.update(kinds)
        for attempt in it.search_attempts:
            kind, text = attempt.get("attempt_type"), (attempt.get("attempt") or "").strip()
            if kind not in kinds or not text:
                continue
            examples = attempt_examples[kind]
            if text not in examples and len(examples) < EXAMPLES_PER_ENTRY:
                examples.append(text)
        for key, value in (it.engagement or {}).items():
            if isinstance(value, int | float):
                engagement[key] += int(value)

    breakdown = Counter(it.breakdown_point or "not_stated" for it in items)
    stated = Counter({k: v for k, v in breakdown.items() if k != "not_stated"})
    rated = [it.rating for it in items if it.rating is not None]
    frustration = [it.frustration_intensity for it in items if it.frustration_intensity]
    strength = [it.evidence_strength for it in items if it.evidence_strength]
    relevance = [it.vague_memory_relevance for it in items if it.vague_memory_relevance is not None]
    vague = retrieval.get("vague_memory_retrieval", 0)
    dates = sorted(it.date for it in items if it.date is not None)
    return {
        "items": n,
        "vague_items": vague,
        "general_items": retrieval.get("general_retrieval", 0),
        "vague_share": _share(vague, n),
        "mean_vague_relevance": _mean(relevance),
        "sources": _ordered(Counter(it.source for it in items)),
        "platforms": _ordered(Counter(it.platform for it in items)),
        "content_types": _ordered(Counter(it.content_type or "unknown" for it in items)),
        "cue_types": _ordered(cue_types),
        "items_with_cues": sum(1 for it in items if it.remembered_cues),
        "top_cues": [
            {"cue": key, "count": count, "examples": cue_examples[key]}
            for key, count in sorted(cue_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CUES]
        ],
        "forgotten": _ordered(forgotten),
        "attempt_types": _ordered(attempt_types),
        "attempt_examples": {k: attempt_examples[k] for k in _ordered(attempt_types)},
        "items_with_attempts": sum(
            1
            for it in items
            if any(a.get("attempt_type") not in (None, "not_stated") for a in it.search_attempts)
        ),
        "breakdown": _ordered(breakdown),
        "dominant_breakdown": stated.most_common(1)[0][0] if stated else "not_stated",
        "emotions": _ordered(Counter(it.emotion or "unknown" for it in items)),
        "outcomes": _ordered(Counter(it.outcome or "not_stated" for it in items)),
        "extraction_categories": _ordered(Counter(it.primary_category or "none" for it in items)),
        "mean_frustration": _mean(frustration),
        "high_stakes_share": _share(sum(1 for it in items if it.high_stakes), n),
        "rated_items": len(rated),
        "low_rating_share": _share(sum(1 for r in rated if r <= 2), len(rated)),
        "mean_evidence_strength": _mean(strength),
        "engagement": dict(engagement),
        "date_range": [dates[0].date().isoformat(), dates[-1].date().isoformat()]
        if dates
        else None,
    }


def select_quotes(
    items: Sequence[EvidenceItem],
    similarity: dict[str, float],
    *,
    max_quotes: int,
    dedup_ratio: float,
) -> list[str]:
    """Item ids of the representative quotes, best first.

    Score = half evidence strength (1-5 scaled to 0-1), half closeness to the area centroid
    (min-max scaled within the area). The best quote of each source is taken first, then
    the rest by score; a quote too close to one already picked is skipped."""
    candidates = [it for it in items if it.quote_grounded and (it.evidence_quote or "").strip()]
    if not candidates:
        return []
    sims = [similarity.get(it.item_id, 0.0) for it in candidates]
    low, high = min(sims), max(sims)
    quote_of = {it.item_id: it.evidence_quote or "" for it in candidates}

    def score(it: EvidenceItem, sim: float) -> float:
        strength = ((it.evidence_strength or 1) - 1) / 4
        closeness = (sim - low) / (high - low) if high > low else 1.0
        return 0.5 * strength + 0.5 * closeness

    def near_duplicate(cid: str, picked: list[str]) -> bool:
        text = quote_of[cid]
        return any(fuzz.token_set_ratio(text, quote_of[p]) >= dedup_ratio for p in picked)

    scored = [
        (it.item_id, it.source, score(it, sim)) for it, sim in zip(candidates, sims, strict=True)
    ]
    return diverse_top(scored, max_quotes, reject=near_duplicate)
