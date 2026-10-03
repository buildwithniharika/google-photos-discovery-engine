"""CSV, PDF, and Sheets exports, including the privacy and spreadsheet edge cases."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from discovery.db import session_scope
from discovery.export.bundle import ExportFilters, ReportBundle, load_bundle
from discovery.export.csv_export import csv_bytes
from discovery.export.dispatch import DispatchError, dispatch_workflow
from discovery.export.pdf_report import build_pdf, glyph_safe
from discovery.export.sanitize import redact_names, spreadsheet_cell, winansi_safe
from discovery.export.sheets_export import (
    SheetsAccessError,
    export_to_sheets,
    workbook,
)
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PublishedRunRow,
    RelevanceRow,
)

WHEN = datetime(2026, 8, 1, tzinfo=UTC)
WEIGHTS = {
    "frequency": 0.2,
    "severity": 0.2,
    "strategic_fit": 0.2,
    "evidence_quality": 0.15,
    "product_leverage": 0.15,
    "research_value": 0.1,
}


def _seed(factory: sessionmaker, run_id: str = "run-1") -> None:
    with session_scope(factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id=run_id, published_at=WHEN))
        session.add(
            ItemRow(
                item_id="plain",
                primary_source_name="play_store",
                platform="Android",
                source_url="https://example.test/plain",
                original_text="original",
                clean_text="I remember my sister Priya's birthday photo but not the date",
                language="en",
                date=WHEN,
                rating=2,
            )
        )
        session.add(
            ItemRow(
                item_id="stakes",
                primary_source_name="app_store",
                platform="iOS",
                source_url="https://example.test/stakes",
                original_text="original stakes",
                clean_text='=HYPERLINK("http://evil") the prescription photo is gone',
                language="en",
                date=WHEN,
                rating=1,
            )
        )
        session.flush()
        session.add(
            InsightRow(
                item_id="plain",
                content_type="photo",
                frustration_intensity=4,
                high_stakes=False,
                emotion="frustrated",
                evidence_quote="my sister Priya's birthday photo",
                problem_statement="cannot find a birthday photo",
                extracted_retrieval_problem="cannot find a birthday photo",
                user_reported_issue="can't find the birthday photo",
                primary_category="life_event_retrieval",
                confidence=0.9,
                remembered_cues=[{"cue": "birthday", "cue_type": "event"}],
                forgotten_details=["exact_date"],
                search_attempts=[{"attempt": "birthday", "attempt_type": "keyword_search"}],
                breakdown_point="query_formulation",
            )
        )
        session.add(
            InsightRow(
                item_id="stakes",
                content_type="document",
                frustration_intensity=5,
                high_stakes=True,
                emotion="anxious",
                evidence_quote="the prescription photo is gone",
                problem_statement="cannot find a prescription",
                primary_category="life_event_retrieval",
                confidence=0.8,
            )
        )
        session.add(
            RelevanceRow(
                item_id="plain",
                stage_reached="C",
                retrieval_type="vague_memory_retrieval",
                vague_memory_relevance=0.9,
                confidence=0.9,
            )
        )
        session.add(
            RelevanceRow(
                item_id="stakes",
                stage_reached="C",
                retrieval_type="vague_memory_retrieval",
                vague_memory_relevance=0.8,
                confidence=0.8,
            )
        )
        session.add(
            OpportunityAreaRow(
                area_id="area-1",
                run_id=run_id,
                name="Screenshot and Document Retrieval Failure",
                category="life_event_retrieval",
                problem_summary="Users remember the purpose of a photo but not the date.",
                aggregates={
                    "items": 2,
                    "dominant_breakdown": "query_formulation",
                    "forgotten": {"exact_date": 1},
                    "top_cues": [{"cue": "birthday", "examples": ["birthday"]}],
                    "attempt_examples": {"keyword_search": ["birthday"]},
                    "why_it_matters": {
                        "text": "Users remember the purpose and not the metadata needed to find it."
                    },
                    "sources": {"play_store": 1},
                    "content_types": {"photo": 1},
                },
                research_questions=[{"question": "What words do they try first?"}],
                status="active",
            )
        )
        session.add(
            OpportunityScoreRow(
                area_id="area-1",
                run_id=run_id,
                frequency=3,
                severity=4,
                strategic_fit=4,
                evidence_quality=3,
                product_leverage=3,
                research_value=3,
                composite=3.4,
                band="Medium",
                weights=WEIGHTS,
                low_evidence_flag=False,
            )
        )
        session.add_all(
            [
                OpportunityEvidenceRow(
                    run_id=run_id,
                    area_id="area-1",
                    item_id="plain",
                    is_representative=True,
                    rank=1,
                ),
                OpportunityEvidenceRow(
                    run_id=run_id,
                    area_id="area-1",
                    item_id="stakes",
                    is_representative=True,
                    rank=2,
                ),
            ]
        )


def _bundle(factory, **filters) -> ReportBundle:
    with session_scope(factory) as session:
        return load_bundle(session, "run-1", ExportFilters(**filters))


def test_formula_prefix_truncation_and_name_redaction():
    assert spreadsheet_cell("=cmd").startswith("'")
    assert spreadsheet_cell("+1").startswith("'")
    assert spreadsheet_cell("-1").startswith("'")
    assert spreadsheet_cell("@sum").startswith("'")
    long = "a" * 60_000
    assert spreadsheet_cell(long).endswith("[truncated]")
    assert len(spreadsheet_cell(long)) <= 50_000
    assert redact_names("my sister Priya called") == "my sister [name] called"


def test_csv_is_bom_utf8_and_stamps_the_run(session_factory):
    _seed(session_factory)
    opportunities, evidence = csv_bytes(_bundle(session_factory))
    assert opportunities.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(opportunities.decode("utf-8-sig"))))
    assert rows[0]["run_id"] == "run-1"
    assert "frequency" in rows[0]["weights"]
    assert "audience=internal" in rows[0]["filters"]
    assert rows[0]["name"] == "Screenshot and Document Retrieval Failure"
    evidence_rows = list(csv.DictReader(io.StringIO(evidence.decode("utf-8-sig"))))
    formula = next(row for row in evidence_rows if row["item_id"] == "stakes")
    assert formula["clean_text"].startswith("'=")


def test_external_csv_drops_sensitive_quotes_and_redacts_names(session_factory):
    _seed(session_factory)
    _opportunities, evidence = csv_bytes(_bundle(session_factory, audience="external"))
    rows = list(csv.DictReader(io.StringIO(evidence.decode("utf-8-sig"))))
    assert [row["item_id"] for row in rows] == ["plain"]
    assert "Priya" not in rows[0]["evidence_quote"]
    assert "[name]" in rows[0]["evidence_quote"]


def test_empty_filter_still_exports_a_note(session_factory):
    _seed(session_factory)
    bundle = _bundle(session_factory, platforms=("YouTube",))
    assert bundle.note.startswith("No matching data")
    opportunities, _evidence = csv_bytes(bundle)
    rows = list(csv.DictReader(io.StringIO(opportunities.decode("utf-8-sig"))))
    assert rows[0]["note"].startswith("No matching data")


def test_pdf_follows_the_example_output_and_omits_sensitive_quotes(session_factory):
    _seed(session_factory)
    bundle = _bundle(session_factory, audience="external")
    pdf = build_pdf(bundle, font="Helvetica")
    assert pdf.startswith(b"%PDF")
    for heading in (
        b"Executive summary",
        b"Opportunity Area:",
        b"Problem Summary",
        b"What Users Remember",
        b"What Users Forget",
        b"Common Search Attempts",
        b"Breakdown Point",
        b"Opportunity Score",
        b"Why This Matters",
        b"Methodology",
        b"Limitations",
        b"run-1",
    ):
        assert heading in pdf
    assert b"prescription" not in pdf
    assert b"Priya" not in pdf


def test_helvetica_replaces_emoji_and_devanagari():
    safe = winansi_safe("hello 😀 नमस्ते")
    assert "😀" not in safe
    assert "न" not in safe
    assert "hello" in safe
    assert "□" in safe
    assert glyph_safe("hello 😀", "Helvetica") == winansi_safe("hello 😀")


def test_sheet_grid_warns_when_the_cell_cap_would_be_exceeded(session_factory):
    _seed(session_factory)
    bundle = _bundle(session_factory)
    grids, warnings = workbook(bundle, max_cells=30)
    assert warnings
    assert "shortened" in warnings[0]
    assert set(grids) == {"Opportunities", "Evidence", "Scoring Weights", "Methodology"}
    assert grids["Scoring Weights"][1][3] == "frequency"


class _Sheet:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[list[str]] = []
        self.cleared = False
        self.calls = 0

    def clear(self) -> None:
        self.cleared = True

    def update(self, rows, _range, value_input_option="RAW") -> None:
        assert value_input_option == "RAW"
        self.calls += 1
        if self.calls == 1 and self.title == "Evidence":
            raise RuntimeError("429 quota")
        self.rows.extend(rows)


class _Book:
    def __init__(self) -> None:
        self.url = "https://docs.google.com/spreadsheets/d/abc"
        self._sheets: dict[str, _Sheet] = {}

    def worksheets(self):
        return list(self._sheets.values())

    def add_worksheet(self, title: str, rows: int, cols: int) -> _Sheet:
        del rows, cols
        sheet = _Sheet(title)
        self._sheets[title] = sheet
        return sheet


def test_sheets_write_retries_quota_and_reports_access_errors(session_factory):
    _seed(session_factory)
    bundle = _bundle(session_factory)
    book = _Book()
    url = export_to_sheets(
        bundle,
        credentials={"client_email": "bot@example.test", "private_key": "x"},
        opener=lambda: book,
    )
    assert url.startswith("https://docs.google.com/")
    assert book._sheets["Evidence"].calls == 2
    assert book._sheets["Opportunities"].rows[1][0] == "run-1"

    def denied():
        raise PermissionError("403 permission denied")

    with pytest.raises(SheetsAccessError, match="bot@example.test"):
        export_to_sheets(
            bundle,
            credentials={"client_email": "bot@example.test", "private_key": "x"},
            spreadsheet_id="sheet-1",
            opener=denied,
        )


def test_dispatch_maps_github_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] != "Bearer good":
            return httpx.Response(401)
        if request.url.path.endswith("/missing.yml/dispatches"):
            return httpx.Response(404)
        assert json.loads(request.content)["ref"] == "main"
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatch_workflow(token="good", repository="acme/photos", client=client)
    with pytest.raises(DispatchError, match="actions:write"):
        dispatch_workflow(token="bad", repository="acme/photos", client=client)
    with pytest.raises(DispatchError, match="missing.yml"):
        dispatch_workflow(
            token="good", repository="acme/photos", workflow="missing.yml", client=client
        )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
