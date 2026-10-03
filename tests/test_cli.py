from __future__ import annotations

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from discovery import cli
from discovery.db import make_engine, make_session_factory, session_scope
from discovery.models.orm import PipelineRunRow

runner = CliRunner()


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cli._context.cache_clear()
    yield url
    cli._context.cache_clear()


def _rows(url: str) -> list[PipelineRunRow]:
    factory = make_session_factory(make_engine(url))
    with session_scope(factory) as s:
        return list(s.scalars(select(PipelineRunRow)).all())


def test_help_lists_all_commands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    names = [
        "ingest",
        "quality",
        "prep",
        "gold-set",
        "classify",
        "extract",
        "extract-review",
        "eval",
        "eval-extract",
        "cluster",
        "curate-area",
        "score",
        "override-score",
        "export",
        "run-all",
        "purge",
        "cost-report",
        "eval-regression",
    ]
    for name in names:
        assert name in result.output


def test_export_without_a_published_run_fails(db_url, tmp_path):
    out = tmp_path / "exports"
    result = runner.invoke(cli.app, ["export", "--format", "pdf", "--out", str(out)])
    assert result.exit_code == 1
    assert "No published run" in result.output
    assert not out.exists()
    assert _rows(db_url) == []


def test_score_without_areas_skips(db_url):
    result = runner.invoke(cli.app, ["score", "--run-id", "no-areas"])
    assert result.exit_code == 0, result.output
    rows = _rows(db_url)
    assert [(r.run_id, r.stage, r.status) for r in rows] == [("no-areas", "score", "skipped")]
    result = runner.invoke(cli.app, ["ingest", "--source", "twitter"])
    assert result.exit_code != 0


class _EmptyConnector:
    warnings: list = []
    stats: dict = {}

    def fetch(self, since, limit):
        return iter(())


def test_run_all_records_every_stage_under_one_run(db_url, monkeypatch, tmp_path):
    from discovery.ingest import runner as ingest_runner

    monkeypatch.setenv("AUTHOR_HASH_SALT", "salt")
    monkeypatch.setenv("DISCOVERY_COST_REPORT", str(tmp_path / "cost.md"))
    monkeypatch.setattr(ingest_runner, "build_connector", lambda s, ctx: _EmptyConnector())
    monkeypatch.setattr(ingest_runner.RawStore, "run_dir", lambda self, source: None)
    result = runner.invoke(cli.app, ["run-all", "--run-id", "full"])
    assert result.exit_code == 0, result.output
    rows = _rows(db_url)
    assert {r.run_id for r in rows} == {"full"}
    assert {r.stage for r in rows} == {
        "run_all",
        "ingest",
        *(f"ingest:{s}" for s in ("play_store", "app_store", "google_sheet", "google_community")),
        "prep",
        "classify",
        "extract",
        "cluster",
        "score",
    }
    run_all = next(r for r in rows if r.stage == "run_all")
    assert run_all.counts["stages"]["score"] == "skipped"
    assert run_all.counts["stages"]["ingest"] == "partial"  # every source returned 0 items


def test_init_db(db_url):
    result = runner.invoke(cli.app, ["init-db"])
    assert result.exit_code == 0
    assert "14 tables ready" in result.output


def test_runs_lists_history(db_url):
    runner.invoke(cli.app, ["prep", "--run-id", "r-hist"])
    result = runner.invoke(cli.app, ["runs"])
    assert result.exit_code == 0
    assert "r-hist" in result.output and "completed" in result.output


def test_classify_on_empty_db_completes(db_url):
    result = runner.invoke(cli.app, ["classify", "--run-id", "c-empty"])
    assert result.exit_code == 0, result.output
    rows = _rows(db_url)
    assert [(r.run_id, r.stage, r.status) for r in rows] == [("c-empty", "classify", "completed")]
    assert rows[0].counts["eligible"] == 0
    result = runner.invoke(cli.app, ["gold-set", "--out", "eval/gold_set.csv"])
    assert result.exit_code == 1
    assert "discovery prep" in result.output


def test_extract_on_empty_db_completes_without_a_client(db_url):
    result = runner.invoke(cli.app, ["extract", "--run-id", "x-empty"])
    assert result.exit_code == 0, result.output
    rows = _rows(db_url)
    assert [(r.run_id, r.stage, r.status) for r in rows] == [("x-empty", "extract", "completed")]
    assert rows[0].counts["in_scope"] == 0
    result = runner.invoke(cli.app, ["extract-review", "--out", "unused.csv"])
    assert result.exit_code == 1
    assert "discovery extract" in result.output


def test_extract_estimate_makes_no_calls_and_is_skipped(db_url):
    result = runner.invoke(cli.app, ["extract", "--estimate", "--run-id", "x-est"])
    assert result.exit_code == 0, result.output
    assert "Cost estimate" in result.output
    rows = _rows(db_url)
    assert [(r.stage, r.status, r.llm_cost_usd) for r in rows] == [("extract", "skipped", 0.0)]


def test_eval_extract_estimate_needs_no_key(db_url):
    result = runner.invoke(cli.app, ["eval-extract", "--estimate"])
    assert result.exit_code == 0, result.output
    assert "Estimated cost" in result.output


def test_llm_command_refuses_while_another_llm_run_is_running(db_url):
    from datetime import UTC, datetime

    factory = make_session_factory(make_engine(db_url))
    cli._context()  # creates the tables
    with session_scope(factory) as s:
        s.add(
            PipelineRunRow(
                run_id="other", stage="extract", status="running", started_at=datetime.now(UTC)
            )
        )
    result = runner.invoke(cli.app, ["llm-check"])
    assert result.exit_code == 1
    assert "Another LLM run is in progress" in result.output


def test_llm_check_without_key_fails_with_clear_message(db_url):
    result = runner.invoke(cli.app, ["llm-check"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_fixture_corpus_runs_from_ingest_through_export(db_url, monkeypatch, tmp_path):
    """One fixture review is ingested and prepped, then exported after the AI stages."""
    from datetime import UTC, datetime

    from discovery.ingest import runner as ingest_runner
    from discovery.models.schemas import Platform, RawItem, SourceName
    from discovery.runs import track_stage

    quote = "I remember the birthday photo but not the date"

    class _OneReview:
        warnings: list = []
        stats = {"pages": 1}

        def fetch(self, since, limit):
            yield RawItem(
                raw_id="play_store:fixture-1",
                source_name=SourceName.PLAY_STORE,
                platform=Platform.ANDROID,
                source_url="https://example.test/fixture-1",
                run_id="fixture",
                fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
                payload={
                    "reviewId": "fixture-1",
                    "content": quote,
                    "score": 2,
                    "thumbsUpCount": 1,
                    "at": "2026-08-01T00:00:00+00:00",
                    "_meta": {"countries": ["us"]},
                },
            )

    def _connector(source, ctx):
        if source == SourceName.PLAY_STORE:
            return _OneReview()
        return _EmptyConnector()

    def _done(stage: str):
        def _run(**kwargs):
            _cfg, factory = cli._context()
            with track_stage(factory, kwargs["run_id"], stage):
                if stage == "score":
                    _publishable_area(factory, kwargs["run_id"], quote)

        return _run

    from discovery.config import IngestSettings

    monkeypatch.setenv("AUTHOR_HASH_SALT", "salt")
    monkeypatch.setenv("DISCOVERY_COST_REPORT", str(tmp_path / "cost.md"))
    monkeypatch.setattr(ingest_runner, "build_connector", _connector)
    monkeypatch.setattr(IngestSettings, "raw_path", property(lambda self: tmp_path / "raw"))
    for stage in ("classify", "extract", "cluster", "score"):
        monkeypatch.setattr(cli, stage, _done(stage))

    result = runner.invoke(cli.app, ["run-all", "--run-id", "fixture"])
    assert result.exit_code == 0, result.output
    assert "Published run fixture." in result.output

    out = tmp_path / "exports"
    csv_result = runner.invoke(
        cli.app, ["export", "--format", "csv", "--run-id", "fixture", "--out", str(out)]
    )
    assert csv_result.exit_code == 0, csv_result.output
    opportunities = (out / "opportunities.csv").read_text(encoding="utf-8-sig")
    evidence = (out / "evidence.csv").read_text(encoding="utf-8-sig")
    assert "fixture" in opportunities
    assert "birthday photo" in evidence
    pdf_result = runner.invoke(
        cli.app,
        ["export", "--format", "pdf", "--audience", "external", "--out", str(out)],
    )
    assert pdf_result.exit_code == 0, pdf_result.output
    pdf = (out / "opportunity_report.pdf").read_bytes()
    assert b"Opportunity Area" in pdf
    assert b"What Users Remember" in pdf
    assert b"birthday" in pdf


def _publishable_area(factory, run_id: str, quote: str) -> None:
    from discovery.models.orm import (
        InsightRow,
        ItemRow,
        OpportunityAreaRow,
        OpportunityEvidenceRow,
        OpportunityScoreRow,
        RelevanceRow,
    )
    from discovery.scoring.dimensions import DIMENSIONS

    weights = {key: 1 / 6 for key in DIMENSIONS}
    with session_scope(factory) as session:
        item = session.scalars(select(ItemRow)).one()
        session.add(
            RelevanceRow(
                item_id=item.item_id,
                stage_reached="C",
                retrieval_type="vague_memory_retrieval",
                vague_memory_relevance=0.9,
                confidence=0.9,
            )
        )
        session.add(
            InsightRow(
                item_id=item.item_id,
                content_type="photo",
                emotion="frustrated",
                frustration_intensity=4,
                high_stakes=False,
                evidence_quote=quote,
                problem_statement="cannot find a birthday photo",
                primary_category="life_event_retrieval",
                remembered_cues=[{"cue": "birthday", "cue_type": "event"}],
                forgotten_details=["exact_date"],
                search_attempts=[{"attempt": "birthday", "attempt_type": "keyword_search"}],
                breakdown_point="query_formulation",
            )
        )
        session.add(
            OpportunityAreaRow(
                area_id="area-1",
                run_id=run_id,
                name="Life event photos",
                category="life_event_retrieval",
                problem_summary="People remember a birthday photo but not the date.",
                aggregates={
                    "why_it_matters": {"text": "A life event is lost when the date is missing."},
                    "top_cues": [{"cue": "birthday"}],
                    "forgotten": {"exact_date": 1},
                    "attempt_examples": {"keyword_search": ["birthday"]},
                    "dominant_breakdown": "query_formulation",
                    "sources": {"play_store": 1},
                    "content_types": {"photo": 1},
                    "items": 1,
                },
                research_questions=[{"question": "What date clues do people still have?"}],
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
                composite=3.3,
                band="Medium",
                weights=weights,
            )
        )
        session.flush()
        session.add(
            OpportunityEvidenceRow(
                run_id=run_id,
                area_id="area-1",
                item_id=item.item_id,
                is_representative=True,
                rank=1,
            )
        )
