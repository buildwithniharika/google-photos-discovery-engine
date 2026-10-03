from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from discovery.db import session_scope
from discovery.models.orm import PipelineRunRow
from discovery.runs import llm_spend_since, new_run_id, running_stages, track_stage


def _row(factory, run_id, stage) -> PipelineRunRow:
    with session_scope(factory) as s:
        return s.get(PipelineRunRow, (run_id, stage))


def test_completed_stage_records_counts_and_usage(session_factory):
    with track_stage(session_factory, "r1", "classify") as run:
        run.count("items", 10)
        run.count("items", 5)
        run.add_llm_usage(1200, 0.0004)
    row = _row(session_factory, "r1", "classify")
    assert row.status == "completed"
    assert row.counts == {"items": 15}
    assert row.llm_tokens == 1200
    assert row.llm_cost_usd == pytest.approx(0.0004)
    assert row.finished_at is not None


def test_llm_spend_since_sums_every_stage_including_failed(session_factory):
    with track_stage(session_factory, "r1", "eval") as run:
        run.add_llm_usage(100, 0.25)
    with pytest.raises(RuntimeError), track_stage(session_factory, "r2", "classify") as run:
        run.add_llm_usage(100, 0.50)
        raise RuntimeError("boom")
    assert llm_spend_since(session_factory) == pytest.approx(0.75)
    future = datetime.now(UTC) + timedelta(days=1)
    assert llm_spend_since(session_factory, future) == 0.0
    assert llm_spend_since(session_factory, future.replace(tzinfo=None)) == 0.0


def test_running_stages_ignores_self_finished_old_and_other_stages(session_factory):
    now = datetime.now(UTC)
    with session_scope(session_factory) as s:
        for run_id, stage, status, started in [
            ("live", "extract", "running", now - timedelta(minutes=5)),
            ("me", "classify", "running", now),
            ("dead", "extract", "running", now - timedelta(hours=7)),
            ("done", "extract", "completed", now),
            ("ingest", "ingest_play_store", "running", now),
        ]:
            s.add(PipelineRunRow(run_id=run_id, stage=stage, status=status, started_at=started))
    found = running_stages(
        session_factory, {"extract", "classify"}, within_hours=6, exclude_run_id="me"
    )
    assert [(run_id, stage) for run_id, stage, _ in found] == [("live", "extract")]


def test_non_fatal_errors_mark_stage_partial(session_factory):
    with track_stage(session_factory, "r1", "ingest") as run:
        run.error("App Store storefront 'nz' returned 503", source="app_store")
    row = _row(session_factory, "r1", "ingest")
    assert row.status == "partial"
    assert row.errors[0]["source"] == "app_store"


def test_exception_marks_stage_failed_and_reraises(session_factory):
    with pytest.raises(RuntimeError), track_stage(session_factory, "r1", "extract"):
        raise RuntimeError("boom")
    row = _row(session_factory, "r1", "extract")
    assert row.status == "failed"
    assert row.errors[0]["message"] == "boom"
    assert row.errors[0]["type"] == "RuntimeError"


def test_skip(session_factory):
    with track_stage(session_factory, "r1", "score") as run:
        run.skip("nothing to do")
    assert _row(session_factory, "r1", "score").status == "skipped"


def test_rerunning_a_stage_overwrites_its_row(session_factory):
    with pytest.raises(ValueError), track_stage(session_factory, "r1", "prep"):
        raise ValueError("first try")
    with track_stage(session_factory, "r1", "prep"):
        pass
    row = _row(session_factory, "r1", "prep")
    assert row.status == "completed"
    assert row.errors == []


def test_run_ids_are_unique_and_sortable():
    ids = [new_run_id() for _ in range(20)]
    assert len(set(ids)) == 20
    assert all(len(i.split("_")[0]) == len("2026-10-05T073012") for i in ids)
