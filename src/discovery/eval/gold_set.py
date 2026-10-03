"""Stratified gold-set sample and labeler-agreement check (architecture Section 17.1).

The sheet is unlabeled. Filling it in is the PM's step (decision D7); this module only
draws the sample, writes the columns, and measures agreement once two labelers have
filled the overlap rows.
"""

from __future__ import annotations

import csv
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.config import PROJECT_ROOT, AppConfig
from discovery.db import session_scope
from discovery.models.orm import ItemRow
from discovery.models.schemas import (
    BreakdownPoint,
    Category,
    ContentType,
    CueType,
    ForgottenDetail,
    RetrievalType,
)
from discovery.prep.clean import contains_phrase, normalize_for_dedup, phrase_pattern
from discovery.prep.dedup import jaccard, shingles

DEFAULT_GOLD_PATH = PROJECT_ROOT / "eval" / "gold_set.csv"
OVERLAP_TARGET = 50
LABEL_FIELDS = (
    "retrieval_type",
    "primary_category",
    "content_type",
    "remembered_cue_types",
    "forgotten_details",
    "breakdown_point",
    "evidence_strength",
)
CONTEXT_FIELDS = (
    "item_id",
    "source",
    "platform",
    "source_url",
    "date",
    "rating",
    "language",
    "stratum",
    "overlap",
    "clean_text",
)
SHEET_FIELDS = (
    *CONTEXT_FIELDS,
    *LABEL_FIELDS,
    "labeler",
    "notes",
    "retrieval_type_2",
    "primary_category_2",
    "labeler_2",
)

STRATA = ("likely_relevant", "borderline", "likely_irrelevant")
SOURCES = ("play_store", "app_store", "google_sheet", "google_community")


@dataclass
class GoldItem:
    item_id: str
    source: str
    platform: str
    source_url: str
    date: str
    rating: str
    language: str
    stratum: str
    overlap: str
    clean_text: str


@dataclass
class SampleResult:
    items: list[GoldItem]
    counts: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_stratum: dict[str, int] = field(default_factory=dict)
    overlap: int = 0
    pool: int = 0


def draw_sample(
    factory: sessionmaker[Session],
    cfg: AppConfig,
    *,
    size: int = 300,
    per_stratum: int = 100,
    seed: int = 0,
    overlap: int = OVERLAP_TARGET,
) -> SampleResult:
    """~100 likely-relevant, ~100 borderline, ~100 likely-irrelevant, spread across sources.

    Only items prep marked ready for the AI stages are eligible, so the set measures the
    text the models will actually see.
    """
    lexicon = cfg.keywords.lexicon
    intent = phrase_pattern(lexicon.retrieval_intent)
    vague = phrase_pattern(lexicon.vague_memory_cues)
    content = phrase_pattern(lexicon.content_types)
    features = phrase_pattern(lexicon.search_features)
    low_rating = cfg.keywords.rules.negative_rating_max

    with session_scope(factory) as session:
        rows = session.execute(
            select(
                ItemRow.item_id,
                ItemRow.primary_source_name,
                ItemRow.platform,
                ItemRow.source_url,
                ItemRow.date,
                ItemRow.rating,
                ItemRow.language,
                ItemRow.clean_text,
                ItemRow.metadata_,
            )
        ).all()

    pool: list[GoldItem] = []
    for row in rows:
        meta = row.metadata_ or {}
        prep = meta.get("prep") if isinstance(meta.get("prep"), dict) else {}
        if not prep.get("ai_eligible") or not row.clean_text:
            continue
        text = row.clean_text
        stratum = _stratum(
            text,
            rating=row.rating,
            intent=intent,
            vague=vague,
            content=content,
            features=features,
            low_rating=low_rating,
        )
        pool.append(
            GoldItem(
                item_id=row.item_id,
                source=row.primary_source_name,
                platform=row.platform,
                source_url=row.source_url,
                date=row.date.date().isoformat() if row.date else "",
                rating="" if row.rating is None else str(row.rating),
                language=row.language or "",
                stratum=stratum,
                overlap="",
                clean_text=text,
            )
        )

    rng = random.Random(seed)
    picked: list[GoldItem] = []
    for stratum in STRATA:
        group = [it for it in pool if it.stratum == stratum]
        picked.extend(_balance_sources(group, per_stratum, rng))
    if len(picked) < size:
        chosen = {it.item_id for it in picked}
        rest = [it for it in pool if it.item_id not in chosen]
        rng.shuffle(rest)
        picked.extend(rest[: size - len(picked)])
    picked = picked[:size]
    _mark_overlap(picked, min(overlap, len(picked)))

    return SampleResult(
        items=picked,
        counts={"pool": len(pool), "sampled": len(picked)},
        by_source=dict(Counter(it.source for it in picked)),
        by_stratum=dict(Counter(it.stratum for it in picked)),
        overlap=sum(it.overlap == "yes" for it in picked),
        pool=len(pool),
    )


def write_sheet(path: Path, sample: SampleResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(SHEET_FIELDS))
        writer.writeheader()
        for item in sample.items:
            row = {name: getattr(item, name, "") for name in CONTEXT_FIELDS}
            for name in SHEET_FIELDS:
                row.setdefault(name, "")
            writer.writerow(row)


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def agreement(rows: list[dict[str, str]]) -> dict[str, object]:
    """Percent agreement on retrieval type and primary category for the overlap rows.

    Rows where either labeler wrote `ambiguous`, or either side is blank, are reported
    separately and left out of the rate (EVAL-02).
    """
    return {
        "retrieval_type": _field_agreement(rows, "retrieval_type", "retrieval_type_2"),
        "primary_category": _field_agreement(rows, "primary_category", "primary_category_2"),
    }


def exemplar_overlap(
    texts: list[str], exemplars: list[str], *, threshold: float = 0.85, shingle_size: int = 5
) -> list[str]:
    """Exemplars that are copied from (or near-copies of) gold-set text (EVAL-01)."""
    if not exemplars or not texts:
        return []
    norm_texts = {normalize_for_dedup(t) for t in texts if t.strip()}
    shingle_sets = [
        shingles(normalize_for_dedup(t).split(), shingle_size) for t in texts if t.strip()
    ]
    hits: list[str] = []
    for exemplar in exemplars:
        key = normalize_for_dedup(exemplar)
        if not key:
            continue
        if key in norm_texts:
            hits.append(exemplar)
            continue
        exemplar_shingles = shingles(key.split(), shingle_size)
        if any(jaccard(exemplar_shingles, existing) >= threshold for existing in shingle_sets):
            hits.append(exemplar)
    return hits


def allowed_values() -> dict[str, list[str]]:
    return {
        "retrieval_type": [m.value for m in RetrievalType] + ["ambiguous"],
        "primary_category": [m.value for m in Category],
        "content_type": [m.value for m in ContentType],
        "remembered_cue_types": [m.value for m in CueType],
        "forgotten_details": [m.value for m in ForgottenDetail],
        "breakdown_point": [m.value for m in BreakdownPoint],
        "evidence_strength": ["1", "2", "3", "4", "5"],
    }


def _stratum(
    text: str,
    *,
    rating: int | None,
    intent: re.Pattern[str],
    vague: re.Pattern[str],
    content: re.Pattern[str],
    features: re.Pattern[str],
    low_rating: int,
) -> str:
    has_intent = contains_phrase(text, intent)
    has_vague = contains_phrase(text, vague)
    has_content = contains_phrase(text, content)
    has_feature = contains_phrase(text, features)
    if has_intent and (has_vague or has_content):
        return "likely_relevant"
    if has_intent or (has_feature and rating is not None and rating <= low_rating):
        return "borderline"
    return "likely_irrelevant"


def _balance_sources(group: list[GoldItem], target: int, rng: random.Random) -> list[GoldItem]:
    by_source: dict[str, list[GoldItem]] = defaultdict(list)
    for item in group:
        by_source[item.source].append(item)
    for bucket in by_source.values():
        bucket.sort(key=lambda it: it.item_id)
        rng.shuffle(bucket)
    present = [src for src in SOURCES if by_source[src]]
    if not present or target <= 0:
        return []
    share = max(1, target // len(present))
    picked: list[GoldItem] = []
    leftover: list[GoldItem] = []
    for src in present:
        bucket = by_source[src]
        take = min(len(bucket), share)
        picked.extend(bucket[:take])
        leftover.extend(bucket[take:])
    if len(picked) < target:
        leftover.sort(key=lambda it: it.item_id)
        rng.shuffle(leftover)
        picked.extend(leftover[: target - len(picked)])
    return picked[:target]


def _mark_overlap(items: list[GoldItem], target: int) -> None:
    """Flag `target` rows, spread across strata, for the second labeler."""
    if target <= 0 or not items:
        return
    by_stratum: dict[str, list[GoldItem]] = defaultdict(list)
    for item in items:
        by_stratum[item.stratum].append(item)
    strata = [s for s in STRATA if by_stratum[s]] or list(by_stratum)
    marked = 0
    cursors = {s: 0 for s in strata}
    while marked < target:
        progressed = False
        for stratum in strata:
            bucket = by_stratum[stratum]
            index = cursors[stratum]
            if index >= len(bucket):
                continue
            bucket[index].overlap = "yes"
            cursors[stratum] = index + 1
            marked += 1
            progressed = True
            if marked >= target:
                break
        if not progressed:
            break


def _field_agreement(rows: list[dict[str, str]], left: str, right: str) -> dict[str, object]:
    compared = 0
    matches = 0
    ambiguous = 0
    unlabeled = 0
    for row in rows:
        if (row.get("overlap") or "").strip().lower() not in ("yes", "y", "1", "true"):
            continue
        a = (row.get(left) or "").strip().lower()
        b = (row.get(right) or "").strip().lower()
        if not a or not b:
            unlabeled += 1
            continue
        if a == "ambiguous" or b == "ambiguous":
            ambiguous += 1
            continue
        compared += 1
        matches += a == b
    rate = (matches / compared) if compared else None
    return {
        "compared": compared,
        "matches": matches,
        "agreement": None if rate is None else round(rate, 4),
        "ambiguous_excluded": ambiguous,
        "unlabeled": unlabeled,
    }
