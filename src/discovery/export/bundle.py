"""Load one run into the tables every export format shares."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from discovery.config import ScoreBands
from discovery.export.sanitize import redact_names
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PMOverrideRow,
    PublishedRunRow,
    RelevanceRow,
)
from discovery.scoring.dimensions import DIMENSIONS
from discovery.scoring.ranker import band_for, weighted_sum

_VAGUE = "vague_memory_retrieval"
_GENERAL = "general_retrieval"


class ExportError(RuntimeError):
    """The export cannot be built from the requested run."""


@dataclass(frozen=True)
class ExportFilters:
    """The filters stamped on every export. An empty tuple means every value."""

    sources: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    severity_min: int = 1
    severity_max: int = 5
    high_stakes_only: bool = False
    retrieval_scope: str = "all"
    vague_min: float = 0.0
    content_types: tuple[str, ...] = ()
    min_confidence: float = 0.0
    date_start: date | None = None
    date_end: date | None = None
    rating_min: int = 1
    rating_max: int = 5
    include_unrated: bool = True
    languages: tuple[str, ...] = ()
    audience: str = "internal"

    def content_active(self) -> bool:
        return any(
            (
                self.sources,
                self.platforms,
                self.categories,
                self.severity_min > 1 or self.severity_max < 5,
                self.high_stakes_only,
                self.retrieval_scope != "all",
                self.vague_min > 0,
                self.content_types,
                self.min_confidence > 0,
                self.date_start is not None or self.date_end is not None,
                self.rating_min > 1 or self.rating_max < 5 or not self.include_unrated,
                self.languages,
            )
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.sources:
            parts.append("source=" + ",".join(self.sources))
        if self.platforms:
            parts.append("platform=" + ",".join(self.platforms))
        if self.categories:
            parts.append("category=" + ",".join(self.categories))
        if self.severity_min > 1 or self.severity_max < 5:
            parts.append(f"severity={self.severity_min}-{self.severity_max}")
        if self.high_stakes_only:
            parts.append("high_stakes")
        if self.retrieval_scope != "all":
            parts.append(f"scope={self.retrieval_scope}")
        if self.vague_min > 0:
            parts.append(f"vague>={self.vague_min:.2f}")
        if self.content_types:
            parts.append("content=" + ",".join(self.content_types))
        if self.min_confidence > 0:
            parts.append(f"confidence>={self.min_confidence:.2f}")
        if self.date_start or self.date_end:
            start = self.date_start.isoformat() if self.date_start else "…"
            end = self.date_end.isoformat() if self.date_end else "…"
            parts.append(f"date={start}..{end}")
        if self.rating_min > 1 or self.rating_max < 5:
            parts.append(f"rating={self.rating_min}-{self.rating_max}")
        if not self.include_unrated:
            parts.append("rated_only")
        if self.languages:
            parts.append("language=" + ",".join(self.languages))
        parts.append(f"audience={self.audience}")
        return "; ".join(parts) if parts else f"audience={self.audience}"

    @classmethod
    def from_any(cls, filters: object, *, audience: str | None = None) -> ExportFilters:
        if isinstance(filters, ExportFilters):
            if audience is None or audience == filters.audience:
                return filters
            data = filters.__dict__.copy()
            data["audience"] = audience
            return cls(**data)
        raw_start = getattr(filters, "date_start", None)
        raw_end = getattr(filters, "date_end", None)
        return cls(
            sources=tuple(getattr(filters, "sources", ()) or ()),
            platforms=tuple(getattr(filters, "platforms", ()) or ()),
            categories=tuple(getattr(filters, "categories", ()) or ()),
            severity_min=int(getattr(filters, "severity_min", 1)),
            severity_max=int(getattr(filters, "severity_max", 5)),
            high_stakes_only=bool(getattr(filters, "high_stakes_only", False)),
            retrieval_scope=str(getattr(filters, "retrieval_scope", "all") or "all"),
            vague_min=float(getattr(filters, "vague_min", 0.0) or 0.0),
            content_types=tuple(getattr(filters, "content_types", ()) or ()),
            min_confidence=float(getattr(filters, "min_confidence", 0.0) or 0.0),
            date_start=_as_date(raw_start),
            date_end=_as_date(raw_end),
            rating_min=int(getattr(filters, "rating_min", 1)),
            rating_max=int(getattr(filters, "rating_max", 5)),
            include_unrated=bool(getattr(filters, "include_unrated", True)),
            languages=tuple(getattr(filters, "languages", ()) or ()),
            audience=audience or "internal",
        )

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ExportFilters:
        unknown = set(payload) - set(cls.__dataclass_fields__)
        if unknown:
            raise ExportError(f"Unknown filter keys: {', '.join(sorted(unknown))}")
        data = dict(payload)
        for key in (
            "sources",
            "platforms",
            "categories",
            "content_types",
            "languages",
        ):
            if key in data and data[key] is not None:
                data[key] = tuple(data[key])
        if data.get("date_start"):
            data["date_start"] = date.fromisoformat(str(data["date_start"]))
        if data.get("date_end"):
            data["date_end"] = date.fromisoformat(str(data["date_end"]))
        return cls(**data)


@dataclass
class EvidenceExport:
    area_id: str
    area_name: str
    item_id: str
    is_representative: bool
    source: str
    platform: str
    source_url: str
    date: str | None
    rating: int | None
    language: str | None
    clean_text: str
    user_reported_issue: str
    extracted_retrieval_problem: str
    evidence_quote: str
    content_type: str
    remembered: str
    forgotten: str
    search_attempts: str
    breakdown_point: str
    retrieval_type: str
    vague_memory_relevance: float | None
    confidence: float | None
    frustration_intensity: int | None
    high_stakes: bool
    emotion: str


@dataclass
class AreaExport:
    rank: int
    area_id: str
    name: str
    category: str
    status: str
    band: str
    composite: float
    scores: dict[str, float]
    low_evidence: bool
    problem_summary: str
    why_it_matters: str
    remembered: list[str]
    forgotten: list[str]
    search_attempts: list[str]
    breakdown_point: str
    quotes: list[tuple[str, str, str]]
    research_questions: list[str]
    sources: str
    content_types: str
    item_count: int


@dataclass
class ReportBundle:
    run_id: str
    exported_at: datetime
    weights: dict[str, float]
    filters: ExportFilters
    areas: list[AreaExport] = field(default_factory=list)
    evidence: list[EvidenceExport] = field(default_factory=list)
    note: str = ""

    @property
    def filters_summary(self) -> str:
        return self.filters.summary()

    def weights_text(self) -> str:
        ordered = {
            key: round(float(self.weights[key]), 4) for key in DIMENSIONS if key in self.weights
        }
        return json.dumps(ordered, separators=(",", ":"))


def resolve_run_id(session: Session, run_id: str | None) -> str:
    if run_id:
        return run_id
    row = session.get(PublishedRunRow, 1)
    if row is None or not row.published_run_id:
        raise ExportError(
            "No published run. Pass --run-id, or publish a run with `discovery score`."
        )
    return row.published_run_id


def load_bundle(
    session: Session,
    run_id: str,
    filters: ExportFilters | None = None,
    *,
    bands: ScoreBands | None = None,
    exported_at: datetime | None = None,
) -> ReportBundle:
    filters = filters or ExportFilters()
    bands = bands or ScoreBands(high=3.8, medium=3.0)
    areas = session.scalars(
        select(OpportunityAreaRow).where(OpportunityAreaRow.run_id == run_id)
    ).all()
    scores = {
        row.area_id: row
        for row in session.scalars(
            select(OpportunityScoreRow).where(OpportunityScoreRow.run_id == run_id)
        ).all()
    }
    overrides = _overrides(session)
    weights = _weights(scores, overrides)
    evidence_rows = _evidence_rows(session, run_id)
    hidden = _irrelevant(overrides)

    kept: list[EvidenceExport] = []
    matched_ids: set[str] = set()
    for evidence, item, insight, relevance, area_name in evidence_rows:
        if item.is_spam or item.item_id in hidden:
            continue
        view = _item_view(item, insight, relevance, overrides)
        if not _matches(view, filters):
            continue
        matched_ids.add(evidence.area_id)
        if filters.audience == "external" and _sensitive(view):
            continue
        text_fields = _externalize(view, filters.audience)
        kept.append(
            EvidenceExport(
                area_id=evidence.area_id,
                area_name=_area_name(evidence.area_id, area_name, overrides),
                item_id=item.item_id,
                is_representative=bool(evidence.is_representative),
                source=item.primary_source_name,
                platform=item.platform,
                source_url=item.source_url,
                date=_iso(item.date),
                rating=item.rating,
                language=item.language,
                clean_text=text_fields["clean_text"],
                user_reported_issue=text_fields["user_reported_issue"],
                extracted_retrieval_problem=text_fields["problem"],
                evidence_quote=text_fields["quote"],
                content_type=view["content_type"] or "",
                remembered=_join_cues(insight.remembered_cues if insight else []),
                forgotten=_join_list(insight.forgotten_details if insight else []),
                search_attempts=_join_attempts(insight.search_attempts if insight else []),
                breakdown_point=(insight.breakdown_point if insight else "") or "",
                retrieval_type=view["retrieval_type"] or "",
                vague_memory_relevance=view["vague"],
                confidence=view["confidence"],
                frustration_intensity=view["frustration"],
                high_stakes=bool(view["high_stakes"]),
                emotion=view["emotion"] or "",
            )
        )

    built: list[AreaExport] = []
    for area in areas:
        status = _override_str(overrides, area.area_id, "status") or area.status
        if status != "active":
            continue
        if filters.content_active() and area.area_id not in matched_ids:
            continue
        score = scores.get(area.area_id)
        if score is None:
            continue
        dimensions = {key: float(getattr(score, key)) for key in DIMENSIONS}
        for key in ("product_leverage", "research_value"):
            replaced = _override_float(overrides, area.area_id, key)
            if replaced is not None:
                dimensions[key] = replaced
        composite = weighted_sum(dimensions, weights)
        agg = _json(area.aggregates, {})
        area_evidence = [row for row in kept if row.area_id == area.area_id]
        quotes = [
            (row.evidence_quote, row.source_url, row.source)
            for row in area_evidence
            if row.is_representative and row.evidence_quote.strip()
        ]
        if not quotes:
            quotes = [
                (row.evidence_quote, row.source_url, row.source)
                for row in area_evidence
                if row.evidence_quote.strip()
            ][:8]
        built.append(
            AreaExport(
                rank=0,
                area_id=area.area_id,
                name=_area_name(area.area_id, area.name, overrides),
                category=area.category,
                status=status,
                band=band_for(composite, bands),
                composite=composite,
                scores=dimensions,
                low_evidence=bool(score.low_evidence_flag),
                problem_summary=area.problem_summary or "",
                why_it_matters=_why(agg),
                remembered=_remembered(agg),
                forgotten=list(_json(agg.get("forgotten"), {}) or {}),
                search_attempts=_attempts(agg),
                breakdown_point=str(agg.get("dominant_breakdown") or ""),
                quotes=quotes,
                research_questions=_questions(area.research_questions),
                sources=_counts(agg.get("sources")),
                content_types=_counts(agg.get("content_types")),
                item_count=len(area_evidence) or int(agg.get("items") or 0),
            )
        )
    built.sort(key=lambda area: (area.low_evidence, -area.composite, area.area_id))
    for index, area in enumerate(built, start=1):
        area.rank = index

    note = ""
    if not built:
        note = (
            "No matching data for the active filters."
            if filters.content_active()
            else "No opportunity areas in this run."
        )
    return ReportBundle(
        run_id=run_id,
        exported_at=exported_at or datetime.now(UTC),
        weights=weights,
        filters=filters,
        areas=built,
        evidence=kept,
        note=note,
    )


def _weights(scores: dict[str, OpportunityScoreRow], overrides: dict) -> dict[str, float]:
    del overrides
    for row in scores.values():
        raw = _json(row.weights, {})
        if raw:
            return {key: float(raw.get(key, 0.0)) for key in DIMENSIONS}
    return {key: 0.0 for key in DIMENSIONS}


def _overrides(session: Session) -> dict[tuple[str, str], Any]:
    found: dict[tuple[str, str], Any] = {}
    rows = session.scalars(select(PMOverrideRow).order_by(PMOverrideRow.override_id)).all()
    for row in rows:
        found[(row.target_id, row.field)] = row.override_value
    return found


def _evidence_rows(session: Session, run_id: str):
    return session.execute(
        select(
            OpportunityEvidenceRow,
            ItemRow,
            InsightRow,
            RelevanceRow,
            OpportunityAreaRow.name,
        )
        .join(ItemRow, ItemRow.item_id == OpportunityEvidenceRow.item_id)
        .outerjoin(InsightRow, InsightRow.item_id == OpportunityEvidenceRow.item_id)
        .outerjoin(RelevanceRow, RelevanceRow.item_id == OpportunityEvidenceRow.item_id)
        .join(
            OpportunityAreaRow,
            (OpportunityAreaRow.area_id == OpportunityEvidenceRow.area_id)
            & (OpportunityAreaRow.run_id == OpportunityEvidenceRow.run_id),
        )
        .where(OpportunityEvidenceRow.run_id == run_id)
    ).all()


def _item_view(
    item: ItemRow,
    insight: InsightRow | None,
    relevance: RelevanceRow | None,
    overrides: dict,
) -> dict:
    item_id = item.item_id
    retrieval = relevance.retrieval_type if relevance else None
    vague = relevance.vague_memory_relevance if relevance else None
    category = insight.primary_category if insight else None
    if (item_id, "retrieval_type") in overrides:
        retrieval = overrides[(item_id, "retrieval_type")]
    if (item_id, "vague_memory_relevance") in overrides:
        vague = overrides[(item_id, "vague_memory_relevance")]
    if (item_id, "primary_category") in overrides:
        category = overrides[(item_id, "primary_category")]
    confidence_values = [
        value
        for value in (
            relevance.confidence if relevance else None,
            insight.confidence if insight else None,
        )
        if value is not None
    ]
    return {
        "source": item.primary_source_name,
        "platform": item.platform,
        "language": item.language,
        "date": item.date,
        "rating": item.rating,
        "category": category,
        "content_type": insight.content_type if insight else None,
        "retrieval_type": retrieval,
        "vague": None if vague is None else float(vague),
        "confidence": min(confidence_values) if confidence_values else None,
        "frustration": insight.frustration_intensity if insight else None,
        "high_stakes": bool(insight.high_stakes) if insight and insight.high_stakes else False,
        "emotion": insight.emotion if insight else None,
        "clean_text": item.clean_text or "",
        "user_reported_issue": (insight.user_reported_issue if insight else "") or "",
        "problem": _problem_text(insight),
        "quote": (insight.evidence_quote if insight else "") or "",
    }


def _matches(view: dict, filters: ExportFilters) -> bool:
    if filters.sources and view["source"] not in filters.sources:
        return False
    if filters.platforms and view["platform"] not in filters.platforms:
        return False
    if filters.languages and view["language"] not in filters.languages:
        return False
    if filters.categories and view["category"] not in filters.categories:
        return False
    if filters.content_types and view["content_type"] not in filters.content_types:
        return False
    when = _as_date(view["date"])
    if filters.date_start and (when is None or when < filters.date_start):
        return False
    if filters.date_end and (when is None or when > filters.date_end):
        return False
    rating = view["rating"]
    if rating is None:
        if not filters.include_unrated:
            return False
    elif rating < filters.rating_min or rating > filters.rating_max:
        return False
    frustration = view["frustration"]
    severity_on = filters.severity_min > 1 or filters.severity_max < 5
    in_band = (
        frustration is not None and filters.severity_min <= frustration <= filters.severity_max
    )
    if severity_on and not in_band:
        return False
    if filters.high_stakes_only and not view["high_stakes"]:
        return False
    retrieval = view["retrieval_type"]
    if filters.retrieval_scope == "vague" and retrieval != _VAGUE:
        return False
    if filters.retrieval_scope == "general" and retrieval != _GENERAL:
        return False
    vague = view["vague"]
    if filters.vague_min > 0 and (vague is None or vague < filters.vague_min):
        return False
    confidence = view["confidence"]
    return not (
        filters.min_confidence > 0 and (confidence is None or confidence < filters.min_confidence)
    )


def _sensitive(view: dict) -> bool:
    """High-stakes (medical, financial, legal) and grief stay out of external reports."""
    return bool(view["high_stakes"]) or view["emotion"] == "sad_loss"


def _externalize(view: dict, audience: str) -> dict[str, str]:
    fields = {
        "clean_text": view["clean_text"],
        "user_reported_issue": view["user_reported_issue"],
        "problem": view["problem"],
        "quote": view["quote"],
    }
    if audience != "external":
        return fields
    return {key: redact_names(value) for key, value in fields.items()}


def _irrelevant(overrides: dict) -> set[str]:
    return {
        item_id
        for (item_id, field), value in overrides.items()
        if field == "marked_irrelevant" and value is True
    }


def _area_name(area_id: str, fallback: str, overrides: dict) -> str:
    return _override_str(overrides, area_id, "name") or fallback


def _override_str(overrides: dict, target: str, field_name: str) -> str | None:
    value = overrides.get((target, field_name))
    if value in (None, ""):
        return None
    return str(value)


def _override_float(overrides: dict, target: str, field_name: str) -> float | None:
    value = overrides.get((target, field_name))
    if value in (None, ""):
        return None
    return float(value)


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _problem_text(insight: InsightRow | None) -> str:
    if insight is None:
        return ""
    return insight.extracted_retrieval_problem or insight.problem_statement or ""


def _join_cues(cues: object) -> str:
    rows = _json(cues, []) or []
    parts = []
    for cue in rows:
        if isinstance(cue, dict):
            text = (cue.get("cue") or "").strip()
            if text:
                parts.append(text)
        elif cue:
            parts.append(str(cue))
    return "; ".join(parts)


def _join_attempts(attempts: object) -> str:
    rows = _json(attempts, []) or []
    parts = []
    for attempt in rows:
        if isinstance(attempt, dict):
            text = (attempt.get("attempt") or "").strip()
            if text:
                parts.append(text)
        elif attempt:
            parts.append(str(attempt))
    return "; ".join(parts)


def _join_list(values: object) -> str:
    rows = _json(values, []) or []
    return "; ".join(str(item) for item in rows if item)


def _counts(value: object) -> str:
    data = _json(value, {}) or {}
    if not isinstance(data, dict):
        return str(data)
    return ", ".join(f"{key} {count}" for key, count in data.items())


def _remembered(agg: dict) -> list[str]:
    cues = _json(agg.get("top_cues"), []) or []
    lines = []
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        examples = cue.get("examples") or []
        if examples:
            lines.extend(str(example) for example in examples)
        elif cue.get("cue"):
            lines.append(str(cue["cue"]))
    return lines


def _attempts(agg: dict) -> list[str]:
    examples = _json(agg.get("attempt_examples"), {}) or {}
    lines = []
    if isinstance(examples, dict):
        for group in examples.values():
            if isinstance(group, list):
                lines.extend(str(item) for item in group)
    return lines


def _why(agg: dict) -> str:
    raw = agg.get("why_it_matters")
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    return str(raw or "")


def _questions(raw: object) -> list[str]:
    rows = _json(raw, []) or []
    found = []
    for row in rows:
        if isinstance(row, dict):
            text = row.get("question") or row.get("text") or ""
            if text:
                found.append(str(text))
        elif row:
            found.append(str(row))
    return found
