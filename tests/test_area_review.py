"""Gate G4 scoring: PM coherence on the opportunity review sheet (Phase 5 criterion)."""

from __future__ import annotations

import csv

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from discovery import cli
from discovery.ai.cluster_stage import write_review_sheet
from discovery.db import session_scope
from discovery.eval.area_review import (
    AreaVerdict,
    curation_commands,
    parse_row,
    record_review,
    render_review,
    review_areas,
    summarize,
)
from discovery.models.orm import PipelineRunRow, PMOverrideRow
from tests.test_cluster_stage import run, seed

MODEL = "BAAI/bge-small-en-v1.5"


def fill(path, scores: dict[int, dict[str, str]]) -> None:
    """Write PM answers into the sheet, keyed by row number (0 = first area)."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    for n, values in scores.items():
        rows[n].update(values)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def reviewed(cfg, session_factory, tmp_path):
    seed(session_factory)
    report = run(session_factory, cfg, "r1")
    sheet = tmp_path / "review.csv"
    write_review_sheet(sheet, report)
    return report, sheet


# --- parsing --------------------------------------------------------------------


def test_parse_row_reads_scores_and_flags_bad_cells():
    ok, problems = parse_row(
        {
            "rank": "2",
            "area_id": "oa-1",
            "name": "'=Area",
            "items": "40",
            "coherence_1_5": "4.0",
            "specific_not_generic": "Yes",
            "action": "Rename",
            "action_detail": "Better name",
        },
        3,
    )
    assert problems == []
    assert (ok.rank, ok.name, ok.items, ok.coherence, ok.specific, ok.action) == (
        2,
        "=Area",
        40,
        4,
        True,
        "rename",
    )
    for bad in ("6", "3.5", "abc", "0"):
        verdict, problems = parse_row({"area_id": "oa-1", "coherence_1_5": bad}, 2)
        assert verdict.coherence is None and "whole number 1-5" in problems[0]
    _, problems = parse_row(
        {"area_id": "oa-1", "specific_not_generic": "maybe", "action": "merge"}, 2
    )
    assert "yes or no" in problems[0] and "target area id" in problems[1]
    _, problems = parse_row({"area_id": "oa-1", "action": "delete"}, 2)
    assert "must be one of" in problems[0]
    assert parse_row({"area_id": ""}, 2) == (None, ["line 2: no area_id"])


def test_summarize_takes_top_active_areas_and_checks_target():
    verdicts = [
        AreaVerdict(rank=n, area_id=f"oa-{n}", name=f"A{n}", status="active", items=10)
        for n in range(1, 11)
    ]
    statuses = {v.area_id: "active" for v in verdicts} | {"oa-2": "archived"}
    for v in verdicts:
        v.coherence = 5 if v.rank % 2 else 3
    summary = summarize(verdicts, "r1", statuses)
    assert [v.area_id for v in summary.top] == [f"oa-{n}" for n in (1, 3, 4, 5, 6, 7, 8, 9)]
    assert summary.mean_top == pytest.approx((5 * 5 + 3 * 3) / 8)
    assert summary.complete and summary.passed

    verdicts[0].coherence = 1
    assert not summarize(verdicts, "r1", statuses).passed
    verdicts[0].coherence = None
    incomplete = summarize(verdicts, "r1", statuses)
    assert not incomplete.complete and not incomplete.passed
    assert "Not checked yet" in render_review(incomplete)


def test_summarize_reports_unknown_missing_and_duplicate_areas():
    verdicts = [
        AreaVerdict(rank=1, area_id="oa-1", name="A", status="active", items=10, coherence=5),
        AreaVerdict(rank=2, area_id="oa-1", name="A", status="active", items=10, coherence=5),
        AreaVerdict(rank=3, area_id="oa-old", name="B", status="active", items=10, coherence=5),
    ]
    summary = summarize(verdicts, "r1", {"oa-1": "active", "oa-2": "active"})
    text = " ".join(summary.problems)
    assert "more than once" in text and "oa-old is not in cluster run r1" in text
    assert "missing from the sheet: oa-2" in text
    assert not summary.passed


def test_curation_commands_quote_names_and_split_cluster_lists():
    verdicts = [
        AreaVerdict(1, "oa-1", "A", "active", 1, action="rename", action_detail="Kid's photos"),
        AreaVerdict(2, "oa-2", "B", "active", 1, action="merge", action_detail="oa-1"),
        AreaVerdict(3, "oa-3", "C", "active", 1, action="split", action_detail="c03, c12"),
        AreaVerdict(4, "oa-4", "D", "active", 1, action="archive"),
        AreaVerdict(5, "oa-5", "E", "active", 1, action="keep"),
    ]
    assert curation_commands(verdicts) == [
        "uv run discovery curate-area oa-1 --rename 'Kid'\"'\"'s photos'",
        "uv run discovery curate-area oa-2 --merge-into oa-1",
        "uv run discovery curate-area oa-3 --split-cluster c03 --split-cluster c12",
        "uv run discovery curate-area oa-4 --archive",
    ]


# --- against a cluster run ------------------------------------------------------


def test_review_scores_a_real_sheet_and_records_once(cfg, session_factory, reviewed):
    report, sheet = reviewed
    fill(
        sheet,
        {
            0: {"coherence_1_5": "5", "specific_not_generic": "yes", "action": "keep"},
            1: {"coherence_1_5": "4", "specific_not_generic": "yes", "pm_notes": "tight"},
            2: {
                "coherence_1_5": "3",
                "specific_not_generic": "no",
                "action": "rename",
                "action_detail": "Search misses places",
            },
        },
    )
    summary = review_areas(session_factory, sheet, MODEL)
    assert summary.problems == [] and summary.run_id == "r1"
    assert len(summary.top) == 3 and summary.mean_top == 4.0 and summary.passed
    assert summary.specific_share == pytest.approx(2 / 3)
    assert set(summary.embedding_coherence) == {a.area_id for a in report.areas}
    assert all(0.9 < c <= 1.0 for c in summary.embedding_coherence.values())

    assert record_review(session_factory, summary) == 6
    assert record_review(session_factory, summary) == 0
    with session_scope(session_factory) as s:
        rows = s.scalars(select(PMOverrideRow).where(PMOverrideRow.field == "coherence")).all()
        assert sorted(r.override_value for r in rows) == [3, 4, 5]
        assert all(r.ai_value > 0.9 and "r1" in r.note for r in rows)

    md = render_review(summary)
    assert "**Met.**" in md and "Low-coherence top areas" in md
    assert "--rename 'Search misses places'" in md

    fill(sheet, {0: {"coherence_1_5": "2"}})
    lower = review_areas(session_factory, sheet, MODEL)
    assert lower.mean_top == 3.0 and not lower.passed and "**Not met.**" in render_review(lower)
    assert record_review(session_factory, lower) == 1


def test_review_needs_a_labeled_cluster_run(session_factory, tmp_path):
    sheet = tmp_path / "review.csv"
    sheet.write_text("rank,area_id,coherence_1_5\n1,oa-1,4\n")
    with pytest.raises(ValueError, match="No LLM-labeled cluster run"):
        review_areas(session_factory, sheet, MODEL)


# --- CLI ------------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cli._context.cache_clear()
    yield url
    cli._context.cache_clear()


def test_cli_review_areas(db_url, tmp_path):
    cfg, factory = cli._context()
    seed(factory)
    sheet, out = tmp_path / "review.csv", tmp_path / "report.md"
    write_review_sheet(sheet, run(factory, cfg, "r1"))
    args = ["review-areas", "--sheet", str(sheet), "--report", str(out)]

    result = runner.invoke(cli.app, [*args, "--run-id", "g4-a"])
    assert result.exit_code == 0, result.output
    assert "0 scored" in result.output and "Not every top area" in result.output

    fill(sheet, {0: {"coherence_1_5": "9"}})
    result = runner.invoke(cli.app, [*args, "--run-id", "g4-b"])
    assert result.exit_code == 1 and "whole number 1-5" in result.output

    fill(sheet, {n: {"coherence_1_5": "4"} for n in range(3)})
    result = runner.invoke(cli.app, [*args, "--run-id", "g4-c"])
    assert result.exit_code == 0, result.output
    assert "criterion met" in result.output and out.exists()
    with session_scope(factory) as s:
        rows = {r.run_id: r for r in s.scalars(select(PipelineRunRow)).all()}
    assert rows["g4-b"].status == "partial" and rows["g4-c"].status == "completed"
    assert rows["g4-c"].counts["passed"] is True and rows["g4-c"].counts["recorded"] == 3
