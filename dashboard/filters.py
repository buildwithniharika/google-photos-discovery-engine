"""Sidebar filter state. An empty tuple means every value of that facet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dashboard.labels import SCOPE_LABELS, SOURCE_LABELS, titleize


@dataclass(frozen=True)
class Filters:
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
    show_irrelevant: bool = False

    def active_count(self) -> int:
        checks = (
            bool(self.sources),
            bool(self.platforms),
            bool(self.categories),
            self.severity_min > 1 or self.severity_max < 5,
            self.high_stakes_only,
            self.retrieval_scope != "all",
            self.vague_min > 0,
            bool(self.content_types),
            self.min_confidence > 0,
            self.date_start is not None or self.date_end is not None,
            self.rating_min > 1 or self.rating_max < 5 or not self.include_unrated,
            bool(self.languages),
        )
        return sum(bool(item) for item in checks)

    def summary(self) -> str:
        parts: list[str] = []
        if self.sources:
            parts.append("Source = " + ", ".join(SOURCE_LABELS.get(s, s) for s in self.sources))
        if self.platforms:
            parts.append("Platform = " + ", ".join(self.platforms))
        if self.categories:
            parts.append("Category = " + ", ".join(titleize(c) for c in self.categories))
        if self.severity_min > 1 or self.severity_max < 5:
            parts.append(f"Severity {self.severity_min}–{self.severity_max}")
        if self.high_stakes_only:
            parts.append("High stakes only")
        if self.retrieval_scope != "all":
            parts.append(SCOPE_LABELS.get(self.retrieval_scope, self.retrieval_scope))
        if self.vague_min > 0:
            parts.append(f"Vague relevance ≥ {self.vague_min:.2f}")
        if self.content_types:
            parts.append("Content = " + ", ".join(titleize(c) for c in self.content_types))
        if self.min_confidence > 0:
            parts.append(f"Confidence ≥ {self.min_confidence:.2f}")
        if self.date_start or self.date_end:
            start = self.date_start.isoformat() if self.date_start else "…"
            end = self.date_end.isoformat() if self.date_end else "…"
            parts.append(f"Date {start} – {end}")
        if self.rating_min > 1 or self.rating_max < 5:
            parts.append(f"Rating {self.rating_min}–{self.rating_max}")
        if not self.include_unrated:
            parts.append("Rated only")
        if self.languages:
            parts.append("Language = " + ", ".join(self.languages))
        if self.show_irrelevant:
            parts.append("Including items marked irrelevant")
        return " · ".join(parts) if parts else "No filters — full published run"


def _as_tuple(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item) for item in value)  # type: ignore[union-attr]


def filters_from_state(state: dict, *, show_irrelevant: bool = False) -> Filters:
    """Read widget keys. Selecting every source is the same as no source filter."""
    sources = _as_tuple(state.get("f_sources"))
    if set(sources) >= set(SOURCE_LABELS):
        sources = ()
    dates = state.get("f_dates") or ()
    date_start = date_end = None
    if isinstance(dates, list | tuple):
        if len(dates) >= 1 and dates[0]:
            date_start = dates[0]
        if len(dates) >= 2 and dates[1]:
            date_end = dates[1]
    severity = state.get("f_severity") or (1, 5)
    rating = state.get("f_rating") or (1, 5)
    scope = state.get("f_scope") or "all"
    return Filters(
        sources=sources,
        platforms=_as_tuple(state.get("f_platforms")),
        categories=_as_tuple(state.get("f_categories")),
        severity_min=int(severity[0]),
        severity_max=int(severity[1]),
        high_stakes_only=bool(state.get("f_stakes")),
        retrieval_scope=str(scope),
        vague_min=float(state.get("f_vague") or 0.0),
        content_types=_as_tuple(state.get("f_content")),
        min_confidence=float(state.get("f_confidence") or 0.0),
        date_start=date_start,
        date_end=date_end,
        rating_min=int(rating[0]),
        rating_max=int(rating[1]),
        include_unrated=bool(state.get("f_unrated", True)),
        languages=_as_tuple(state.get("f_languages")),
        show_irrelevant=show_irrelevant,
    )
