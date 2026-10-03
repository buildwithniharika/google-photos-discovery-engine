"""Google Sheets export: Opportunities, Evidence, Scoring Weights, Methodology."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from discovery.export.bundle import ReportBundle
from discovery.export.csv_export import _evidence_rows, _opportunity_rows
from discovery.export.sanitize import spreadsheet_cell
from discovery.scoring.dimensions import DIMENSIONS

MAX_CELLS = 10_000_000
CHUNK_ROWS = 500
TAB_OPPORTUNITIES = "Opportunities"
TAB_EVIDENCE = "Evidence"
TAB_WEIGHTS = "Scoring Weights"
TAB_METHODOLOGY = "Methodology"

METHODOLOGY_TEXT = (
    "This spreadsheet is a reproducible export of one discovery run. "
    "Every tab carries the run id, the export date, the scoring weights, and the active filters. "
    "Opportunities is one row per ranked area. Evidence is one row per supporting item, using "
    "PII-redacted text. Scoring Weights is the model used for the composite. "
    "An external audience omits high-stakes and grief items and replaces some personal names "
    "with [name]. Quotes are spans of the redacted text, shown without author names. "
    "The ranking is research input. It is not a design for Google Photos search."
)


class SheetsError(RuntimeError):
    """Sheets export could not be written."""


class SheetsAccessError(SheetsError):
    """The service account cannot open the target spreadsheet (EXP-06)."""


def load_service_account(explicit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Credentials from the caller, GOOGLE_SERVICE_ACCOUNT_JSON, or a file path."""
    if explicit:
        return _normalize_key(dict(explicit))
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        import json

        return _normalize_key(json.loads(raw))
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path:
        import json

        return _normalize_key(json.loads(Path(path).read_text(encoding="utf-8")))
    raise SheetsError(
        "No Google service account. Set GOOGLE_SERVICE_ACCOUNT_FILE or "
        "GOOGLE_SERVICE_ACCOUNT_JSON, or add [gcp_service_account] to Streamlit secrets."
    )


def workbook(
    bundle: ReportBundle, *, max_cells: int = MAX_CELLS
) -> tuple[dict[str, list[list[str]]], list[str]]:
    """Tab name to a grid of strings. Evidence is trimmed if the sheet would exceed the cell cap."""
    warnings: list[str] = []
    opportunities = _grid(_opportunity_rows(bundle))
    evidence_dicts = _evidence_rows(bundle)
    evidence = _grid(evidence_dicts)
    weights = _weights_grid(bundle)
    methodology = _methodology_grid(bundle)
    grids = {
        TAB_OPPORTUNITIES: opportunities,
        TAB_EVIDENCE: evidence,
        TAB_WEIGHTS: weights,
        TAB_METHODOLOGY: methodology,
    }
    total = sum(_cells(rows) for rows in grids.values())
    if total > max_cells:
        other = total - _cells(evidence)
        width = max(len(evidence[0]) if evidence else 1, 1)
        room = max(max_cells - other, width)
        keep_rows = max(1, room // width)
        evidence = evidence[:keep_rows]
        grids[TAB_EVIDENCE] = evidence
        warnings.append(
            "Evidence was shortened so the spreadsheet stays under the Google Sheets cell limit. "
            f"Kept {max(keep_rows - 1, 0)} data rows."
        )
    return grids, warnings


def export_to_sheets(
    bundle: ReportBundle,
    *,
    credentials: dict[str, Any] | None = None,
    spreadsheet_id: str | None = None,
    title: str | None = None,
    opener: Callable[..., Any] | None = None,
) -> str:
    """Write the four tabs and return the spreadsheet URL."""
    creds = load_service_account(credentials)
    grids, warnings = workbook(bundle)
    bundle_warnings = warnings
    open_book = opener or (lambda: _open_gspread(creds, spreadsheet_id, title or _title(bundle)))
    try:
        book = open_book()
    except SheetsAccessError:
        raise
    except Exception as exc:
        if _access_denied(exc):
            email = creds.get("client_email") or "the service account"
            raise SheetsAccessError(
                f"The service account {email} cannot open that spreadsheet. "
                "Share the spreadsheet with that email as an Editor, then export again."
            ) from exc
        raise SheetsError(str(exc)) from exc
    _push(book, grids)
    url = getattr(book, "url", "") or ""
    if bundle_warnings:
        url = url + ("\n" if url else "") + " ".join(bundle_warnings)
    return url


def _title(bundle: ReportBundle) -> str:
    return f"Discovery opportunities {bundle.run_id}"


def _grid(rows: list[dict[str, object]]) -> list[list[str]]:
    if not rows:
        return [[]]
    columns = list(rows[0])
    grid = [columns]
    for row in rows:
        grid.append([spreadsheet_cell(row.get(column)) for column in columns])
    return grid


def _weights_grid(bundle: ReportBundle) -> list[list[str]]:
    header = ["run_id", "exported_at", "filters", "dimension", "weight"]
    rows = [header]
    for key in DIMENSIONS:
        rows.append(
            [
                bundle.run_id,
                bundle.exported_at.date().isoformat(),
                bundle.filters_summary,
                key,
                f"{float(bundle.weights.get(key, 0)):.4f}",
            ]
        )
    return rows


def _methodology_grid(bundle: ReportBundle) -> list[list[str]]:
    return [
        ["run_id", "exported_at", "filters", "weights", "methodology"],
        [
            bundle.run_id,
            bundle.exported_at.date().isoformat(),
            bundle.filters_summary,
            bundle.weights_text(),
            METHODOLOGY_TEXT,
        ],
    ]


def _cells(rows: list[list[str]]) -> int:
    if not rows:
        return 0
    width = max(len(row) for row in rows)
    return width * len(rows)


def _push(book: Any, grids: dict[str, list[list[str]]]) -> None:
    existing = {ws.title: ws for ws in book.worksheets()}
    for title, rows in grids.items():
        width = max((len(row) for row in rows), default=1)
        height = max(len(rows), 1)
        sheet = existing.get(title)
        if sheet is None:
            sheet = book.add_worksheet(title=title, rows=height, cols=width)
        else:
            sheet.clear()
        _write_chunks(sheet, rows)


def _write_chunks(sheet: Any, rows: list[list[str]]) -> None:
    if not rows:
        return
    for start in range(0, len(rows), CHUNK_ROWS):
        chunk = rows[start : start + CHUNK_ROWS]
        _update_chunk(sheet, chunk, start + 1)


def _is_quota(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "rate" in text


@retry(
    retry=retry_if_exception(_is_quota),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)
def _update_chunk(sheet: Any, rows: list[list[str]], start_row: int) -> None:
    sheet.update(rows, f"A{start_row}", value_input_option="RAW")


def _open_gspread(credentials: dict[str, Any], spreadsheet_id: str | None, title: str) -> Any:
    import gspread

    client = gspread.service_account_from_dict(credentials)
    if spreadsheet_id:
        return client.open_by_key(spreadsheet_id)
    return client.create(title)


def _access_denied(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("403", "404", "permission", "not found", "forbidden"))


def _normalize_key(data: dict[str, Any]) -> dict[str, Any]:
    key = data.get("private_key")
    if isinstance(key, str) and "\\n" in key:
        data["private_key"] = key.replace("\\n", "\n")
    return data
