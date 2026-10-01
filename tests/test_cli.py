from __future__ import annotations

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from discovery import cli
from discovery.db import make_engine, make_session_factory, session_scope
from discovery.models.orm import PipelineRunRow

STUB_COMMANDS = {
    "prep": [],
    "classify": [],
    "extract": [],
    "cluster": [],
    "score": [],
    "export": ["--format", "pdf"],
    "eval": [],
}

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
    for name in [*STUB_COMMANDS, "ingest", "quality", "run-all"]:
        assert name in result.output


@pytest.mark.parametrize("command", sorted(STUB_COMMANDS))
def test_stub_command_exits_cleanly_and_is_logged(db_url, command):
    result = runner.invoke(cli.app, [command, *STUB_COMMANDS[command], "--run-id", "test-run"])
    assert result.exit_code == 0, result.output
    rows = _rows(db_url)
    assert [(r.run_id, r.stage, r.status) for r in rows] == [("test-run", command, "skipped")]
    assert rows[0].finished_at is not None


def test_ingest_validates_source(db_url):
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
    assert "r-hist" in result.output and "skipped" in result.output


def test_llm_check_without_key_fails_with_clear_message(db_url):
    result = runner.invoke(cli.app, ["llm-check"])
    assert result.exit_code == 1
    assert "GROQ_API_KEY" in result.output
