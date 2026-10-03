"""opportunities.csv and evidence.csv, in memory (architecture Section 12)."""

from __future__ import annotations

import csv
import io

from discovery.export.bundle import ReportBundle
from discovery.export.sanitize import spreadsheet_cell
from discovery.scoring.dimensions import DIMENSIONS

OPPORTUNITY_COLUMNS = (
    "run_id",
    "exported_at",
    "weights",
    "filters",
    "note",
    "rank",
    "area_id",
    "name",
    "category",
    "band",
    "composite",
    "low_evidence",
    *DIMENSIONS,
    "problem_summary",
    "why_it_matters",
    "sources",
    "content_types",
    "remembered",
    "forgotten",
    "search_attempts",
    "breakdown_point",
    "representative_quotes",
    "source_links",
    "research_questions",
    "item_count",
)

EVIDENCE_COLUMNS = (
    "run_id",
    "exported_at",
    "weights",
    "filters",
    "note",
    "area_id",
    "area_name",
    "item_id",
    "is_representative",
    "source",
    "platform",
    "source_url",
    "date",
    "rating",
    "language",
    "clean_text",
    "user_reported_issue",
    "extracted_retrieval_problem",
    "evidence_quote",
    "content_type",
    "remembered",
    "forgotten",
    "search_attempts",
    "breakdown_point",
    "retrieval_type",
    "vague_memory_relevance",
    "confidence",
    "frustration_intensity",
    "high_stakes",
)


def csv_bytes(bundle: ReportBundle) -> tuple[bytes, bytes]:
    """UTF-8 with BOM, so Excel keeps emoji and non-Latin text (EXP-02)."""
    return (
        _encode(OPPORTUNITY_COLUMNS, _opportunity_rows(bundle)),
        _encode(EVIDENCE_COLUMNS, _evidence_rows(bundle)),
    )


def _encode(columns: tuple[str, ...] | list[str], rows: list[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), lineterminator="\n", extrasaction="ignore"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({key: spreadsheet_cell(row.get(key)) for key in columns})
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _stamp(bundle: ReportBundle) -> dict[str, object]:
    return {
        "run_id": bundle.run_id,
        "exported_at": bundle.exported_at.date().isoformat(),
        "weights": bundle.weights_text(),
        "filters": bundle.filters_summary,
        "note": bundle.note,
    }


def _opportunity_rows(bundle: ReportBundle) -> list[dict[str, object]]:
    if not bundle.areas:
        return [{**_stamp(bundle), "name": bundle.note or "No matching data"}]
    rows = []
    for area in bundle.areas:
        row: dict[str, object] = {
            **_stamp(bundle),
            "rank": area.rank,
            "area_id": area.area_id,
            "name": area.name,
            "category": area.category,
            "band": area.band,
            "composite": f"{area.composite:.2f}",
            "low_evidence": area.low_evidence,
            "problem_summary": area.problem_summary,
            "why_it_matters": area.why_it_matters,
            "sources": area.sources,
            "content_types": area.content_types,
            "remembered": "; ".join(area.remembered),
            "forgotten": "; ".join(area.forgotten),
            "search_attempts": "; ".join(area.search_attempts),
            "breakdown_point": area.breakdown_point,
            "representative_quotes": " | ".join(quote for quote, _url, _source in area.quotes),
            "source_links": " | ".join(url for _quote, url, _source in area.quotes if url),
            "research_questions": " | ".join(area.research_questions),
            "item_count": area.item_count,
        }
        row.update({key: f"{area.scores[key]:.2f}" for key in DIMENSIONS})
        rows.append(row)
    return rows


def _evidence_rows(bundle: ReportBundle) -> list[dict[str, object]]:
    if not bundle.evidence:
        return [{**_stamp(bundle), "clean_text": bundle.note or "No matching data"}]
    rows = []
    for item in bundle.evidence:
        rows.append(
            {
                **_stamp(bundle),
                "area_id": item.area_id,
                "area_name": item.area_name,
                "item_id": item.item_id,
                "is_representative": item.is_representative,
                "source": item.source,
                "platform": item.platform,
                "source_url": item.source_url,
                "date": item.date or "",
                "rating": "" if item.rating is None else item.rating,
                "language": item.language or "",
                "clean_text": item.clean_text,
                "user_reported_issue": item.user_reported_issue,
                "extracted_retrieval_problem": item.extracted_retrieval_problem,
                "evidence_quote": item.evidence_quote,
                "content_type": item.content_type,
                "remembered": item.remembered,
                "forgotten": item.forgotten,
                "search_attempts": item.search_attempts,
                "breakdown_point": item.breakdown_point,
                "retrieval_type": item.retrieval_type,
                "vague_memory_relevance": ""
                if item.vague_memory_relevance is None
                else f"{item.vague_memory_relevance:.2f}",
                "confidence": "" if item.confidence is None else f"{item.confidence:.2f}",
                "frustration_intensity": ""
                if item.frustration_intensity is None
                else item.frustration_intensity,
                "high_stakes": item.high_stakes,
            }
        )
    return rows
