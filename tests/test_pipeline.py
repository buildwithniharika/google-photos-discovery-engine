"""Publish checks, the pipeline lock, purge, and the cost report."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from discovery.db import (
    PipelineBusy,
    init_db,
    make_engine,
    pipeline_lock,
    session_scope,
)
from discovery.models.orm import (
    InsightRow,
    ItemRow,
    LLMCacheRow,
    OpportunityAreaRow,
    OpportunityEvidenceRow,
    OpportunityScoreRow,
    PipelineRunRow,
    PublishedRunRow,
    RawItemRow,
)
from discovery.models.schemas import RunStatus
from discovery.pipeline import (
    decide_publish,
    parse_older_than,
    purge_older_than,
    render_cost_report,
)

WHEN = datetime(2026, 1, 1, tzinfo=UTC)
WEIGHTS = {
    "frequency": 0.2,
    "severity": 0.2,
    "strategic_fit": 0.2,
    "evidence_quality": 0.15,
    "product_leverage": 0.15,
    "research_value": 0.1,
}
STAGES = ("ingest", "prep", "classify", "extract", "cluster", "score")


def _stage(run_id: str, stage: str, status: str = "completed") -> PipelineRunRow:
    return PipelineRunRow(
        run_id=run_id,
        stage=stage,
        started_at=WHEN,
        finished_at=WHEN + timedelta(minutes=1),
        status=status,
        llm_tokens=10,
        llm_cost_usd=0.01,
    )


def _area(run_id: str, area_id: str = "area-1"):
    return (
        OpportunityAreaRow(
            area_id=area_id,
            run_id=run_id,
            name="Milestone photos",
            category="life_event_retrieval",
            problem_summary="Users cannot find a milestone.",
            status="active",
            aggregates={"dominant_breakdown": "query_formulation"},
        ),
        OpportunityScoreRow(
            area_id=area_id,
            run_id=run_id,
            frequency=3,
            severity=3,
            strategic_fit=3,
            evidence_quality=3,
            product_leverage=3,
            research_value=3,
            composite=3,
            band="Medium",
            weights=WEIGHTS,
            low_evidence_flag=False,
        ),
    )


def _add_quote(session, run_id: str, item_id: str, area_id: str = "area-1") -> None:
    session.add(
        ItemRow(
            item_id=item_id,
            primary_source_name="play_store",
            platform="Android",
            source_url=f"https://example.test/{item_id}",
            original_text="original",
            clean_text=f"clean {item_id}",
        )
    )
    session.flush()
    session.add(InsightRow(item_id=item_id, evidence_quote=f"quote {item_id}"))
    session.add(
        OpportunityEvidenceRow(
            run_id=run_id, area_id=area_id, item_id=item_id, is_representative=True
        )
    )


def test_publish_keeps_the_previous_run_when_a_quote_is_missing(session_factory):
    with session_scope(session_factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id="old", published_at=WHEN))
        session.add_all(_stage("new", stage) for stage in STAGES)
        area, score = _area("new")
        session.add_all([area, score])
    # Scoring moves the pointer before the sanity check. A failed check puts it back.
    with session_scope(session_factory) as session:
        row = session.get(PublishedRunRow, 1)
        row.published_run_id = "new"
        row.published_at = WHEN + timedelta(hours=1)
    outcome = decide_publish(session_factory, "new", ("old", WHEN), stage_failed=False)
    assert outcome.published is False
    assert outcome.status == "partial"
    assert any("representative quote" in reason for reason in outcome.reasons)
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == "old"


def test_publish_moves_the_pointer_when_checks_pass(session_factory):
    with session_scope(session_factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id="old", published_at=WHEN))
        session.add_all(_stage("new", stage) for stage in STAGES)
        area, score = _area("new")
        session.add_all([area, score])
        _add_quote(session, "new", "item-1")
    outcome = decide_publish(session_factory, "new", ("old", WHEN), stage_failed=False)
    assert outcome.published is True
    assert outcome.status == "published"
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == "new"


def test_a_volume_drop_is_not_published(session_factory):
    with session_scope(session_factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id="old", published_at=WHEN))
        session.add_all(_stage("new", stage) for stage in STAGES)
        old_area, old_score = _area("old")
        new_area, new_score = _area("new")
        session.add_all([old_area, old_score, new_area, new_score])
        for index in range(12):
            _add_quote(session, "old", f"old-{index}")
        _add_quote(session, "new", "new-1")
    outcome = decide_publish(session_factory, "new", ("old", WHEN), stage_failed=False)
    assert outcome.published is False
    assert any("90%" in reason for reason in outcome.reasons)
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == "old"


def test_skipped_score_leaves_the_pointer(session_factory):
    with session_scope(session_factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id="old", published_at=WHEN))
        session.add(_stage("new", "score", status=RunStatus.SKIPPED.value))
    outcome = decide_publish(session_factory, "new", ("old", WHEN), stage_failed=False)
    assert outcome.status == "unchanged"
    with session_scope(session_factory) as session:
        assert session.get(PublishedRunRow, 1).published_run_id == "old"


def test_lock_is_exclusive_and_reentrant(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'lock.db'}")
    init_db(engine)
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with pipeline_lock(engine, "holder"):
            started.set()
            assert release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(5)
    with pytest.raises(PipelineBusy), pipeline_lock(engine, "other"):
        pass
    with pipeline_lock(engine, "holder"):
        pass
    release.set()
    thread.join(5)
    with pipeline_lock(engine, "other"):
        pass
    engine.dispose()


def test_purge_keeps_the_published_run_and_drops_older_raw_data(session_factory, tmp_path):
    old = WHEN
    recent = datetime.now(UTC)
    raw_dir = tmp_path / "raw" / "play_store"
    (raw_dir / "old-run").mkdir(parents=True)
    (raw_dir / "old-run" / "items.jsonl").write_text("x", encoding="utf-8")
    (raw_dir / "published").mkdir(parents=True)
    with session_scope(session_factory) as session:
        session.add(PublishedRunRow(id=1, published_run_id="published", published_at=recent))
        session.add(
            RawItemRow(
                raw_id="r1",
                source_name="play_store",
                run_id="old-run",
                fetched_at=old,
                payload={},
            )
        )
        session.add(
            RawItemRow(
                raw_id="r2",
                source_name="play_store",
                run_id="published",
                fetched_at=old,
                payload={"keep": True},
            )
        )
        session.add(
            LLMCacheRow(
                cache_key="k",
                model="m",
                prompt_version="v",
                response={},
                created_at=old,
            )
        )
        session.add(_stage("old-run", "score"))
        session.add(_stage("published", "score", status="published"))
        area, score = _area("old-run")
        session.add_all([area, score])
    cutoff = parse_older_than("30d")
    report = purge_older_than(session_factory, cutoff, raw_dir=tmp_path / "raw", dry_run=False)
    assert report.protected_run_id == "published"
    assert report.raw_items == 1
    assert report.runs == 1
    assert not (raw_dir / "old-run").exists()
    assert (raw_dir / "published").exists()
    with session_scope(session_factory) as session:
        remaining = session.scalars(select(RawItemRow.raw_id)).all()
        assert remaining == ["r2"]
        assert session.get(OpportunityAreaRow, ("area-1", "old-run")) is None
        assert session.get(PublishedRunRow, 1).published_run_id == "published"
        assert session.scalar(select(LLMCacheRow)) is None


def test_cost_report_names_the_budget_and_rate_limits(session_factory, cfg):
    with session_scope(session_factory) as session:
        row = _stage("costed", "classify")
        row.errors = [{"message": "HTTP 429 retry"}]
        session.add(row)
    text = render_cost_report(session_factory, cfg, run_id="costed")
    assert "Section 19" in text
    assert "$10.50" in text or "10.50" in text
    assert "429" in text
    assert "Tokens per minute" in text
