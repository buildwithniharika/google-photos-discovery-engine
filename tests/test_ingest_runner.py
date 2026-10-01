from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from discovery import cli
from discovery.config import load_config
from discovery.db import make_engine, make_session_factory, session_scope
from discovery.ingest import runner
from discovery.ingest.base import RawStore, SourceError
from discovery.models.orm import ItemRow, PipelineRunRow, RawItemRow
from discovery.models.schemas import Platform, RawItem, RunStatus, SourceName
from discovery.quality import quality_report, render_markdown

T0 = datetime(2026, 9, 30, tzinfo=UTC)


def play_raw(rid: str, run_id: str, *, content: str = "can't find a photo", days_ago: int = 0):
    return RawItem(
        raw_id=f"play_store:{rid}",
        source_name=SourceName.PLAY_STORE,
        platform=Platform.ANDROID,
        source_url=f"https://play.google.com/store/apps/details?id=x&reviewId={rid}",
        run_id=run_id,
        payload={
            "reviewId": rid,
            "content": content,
            "score": 2,
            "thumbsUpCount": 0,
            "at": (T0 - timedelta(days=days_ago)).isoformat(),
            "author_hash": None,
            "_meta": {"countries": ["in"]},
        },
    )


class FakeConnector:
    def __init__(self, source: SourceName, items=(), error: Exception | None = None):
        self.source_name = source
        self.platform = Platform.ANDROID
        self.items = list(items)
        self.error = error
        self.warnings = []
        self.stats = {"pages": 1}
        self.calls = []

    def fetch(self, since, limit):
        self.calls.append((since, limit))
        yield from self.items
        if self.error:
            raise self.error


def factory_for(connectors: dict[SourceName, FakeConnector]):
    return lambda source, ctx: connectors[source]


@pytest.fixture
def cfg(cfg, tmp_path):
    cfg.settings.ingest.raw_dir = str(tmp_path / "raw")
    return cfg


def test_one_failing_source_does_not_stop_the_others(cfg, session_factory, tmp_path):
    connectors = {
        SourceName.PLAY_STORE: FakeConnector(
            SourceName.PLAY_STORE, [play_raw("a", "r1")], error=SourceError("blocked mid-run")
        ),
        SourceName.APP_STORE: FakeConnector(SourceName.APP_STORE, error=RuntimeError("bug")),
        SourceName.GOOGLE_SHEET: FakeConnector(SourceName.GOOGLE_SHEET),
        SourceName.GOOGLE_COMMUNITY: FakeConnector(
            SourceName.GOOGLE_COMMUNITY, [play_raw("b", "r1")]
        ),
    }
    outcomes = runner.run_ingest(
        cfg,
        session_factory,
        "r1",
        runner.ALL_SOURCES,
        salt="s",
        connector_factory=factory_for(connectors),
    )
    status = {o.source: o.status for o in outcomes}
    assert status == {
        SourceName.PLAY_STORE: RunStatus.PARTIAL,  # items before the block are kept
        SourceName.APP_STORE: RunStatus.FAILED,
        SourceName.GOOGLE_SHEET: RunStatus.PARTIAL,  # zero items (ING-X-01)
        SourceName.GOOGLE_COMMUNITY: RunStatus.COMPLETED,
    }
    with session_scope(session_factory) as s:
        rows = {r.stage: r for r in s.scalars(select(PipelineRunRow))}
        assert rows["ingest"].status == "partial"
        assert rows["ingest:play_store"].counts["items_new"] == 1
        assert rows["ingest:play_store"].counts["stat_pages"] == 1
        assert "blocked mid-run" in rows["ingest:play_store"].errors[0]["message"]
        assert "0 items" in rows["ingest:google_sheet"].errors[0]["message"]
        assert rows["ingest:play_store"].counts["robots_exception"].startswith("PM decision")
        assert s.scalar(select(func.count()).select_from(ItemRow)) == 2
    assert (tmp_path / "raw" / "play_store" / "r1" / "items.jsonl").exists()


def test_reingest_adds_only_new_items_and_never_deletes(cfg, session_factory):
    first = {
        SourceName.PLAY_STORE: FakeConnector(
            SourceName.PLAY_STORE, [play_raw("a", "r1"), play_raw("b", "r1")]
        )
    }
    runner.run_ingest(
        cfg,
        session_factory,
        "r1",
        [SourceName.PLAY_STORE],
        salt="s",
        connector_factory=factory_for(first),
    )
    second = {
        SourceName.PLAY_STORE: FakeConnector(
            SourceName.PLAY_STORE, [play_raw("b", "r2"), play_raw("c", "r2")]
        )
    }
    [outcome] = runner.run_ingest(
        cfg,
        session_factory,
        "r2",
        [SourceName.PLAY_STORE],
        salt="s",
        connector_factory=factory_for(second),
    )
    assert (outcome.counts["items_new"], outcome.counts["items_unchanged"]) == (1, 1)
    assert (outcome.counts["raw_new"], outcome.counts["raw_unchanged"]) == (1, 1)
    with session_scope(session_factory) as s:
        assert s.scalar(select(func.count()).select_from(RawItemRow)) == 3  # 'a' kept


def test_since_last_uses_newest_item_minus_overlap(cfg, session_factory):
    seed = {
        SourceName.PLAY_STORE: FakeConnector(
            SourceName.PLAY_STORE, [play_raw("a", "r1", days_ago=10), play_raw("b", "r1")]
        )
    }
    runner.run_ingest(
        cfg,
        session_factory,
        "r1",
        [SourceName.PLAY_STORE],
        salt="s",
        connector_factory=factory_for(seed),
    )
    nxt = FakeConnector(SourceName.PLAY_STORE)
    runner.run_ingest(
        cfg,
        session_factory,
        "r2",
        [SourceName.PLAY_STORE],
        since="last",
        salt="s",
        connector_factory=factory_for({SourceName.PLAY_STORE: nxt}),
    )
    assert nxt.calls[0][0] == T0 - timedelta(days=cfg.settings.ingest.since_overlap_days)


def test_parse_since():
    assert runner.parse_since("2026-09-01") == datetime(2026, 9, 1, tzinfo=UTC)
    assert runner.parse_since("last") is None and runner.parse_since(None) is None
    with pytest.raises(ValueError):
        runner.parse_since("yesterday-ish")


def test_raw_store_updates_meta_without_counting_a_change(session_factory):
    store = RawStore(session_factory, "r1", None)
    item = play_raw("a", "r1")
    assert store.write([item]).new == 1
    moved = item.model_copy(
        update={"payload": {**item.payload, "_meta": {"countries": ["in", "us"]}}}
    )
    assert store.write([moved]).unchanged == 1
    with session_scope(session_factory) as s:
        row = s.get(RawItemRow, "play_store:a")
        assert row.payload["_meta"]["countries"] == ["in", "us"]
        assert row.payload["_meta"]["platform"] == "Android"


def test_quality_report(cfg, session_factory):
    connectors = {
        SourceName.PLAY_STORE: FakeConnector(
            SourceName.PLAY_STORE, [play_raw("a", "r1", days_ago=5), play_raw("b", "r1")]
        )
    }
    runner.run_ingest(
        cfg,
        session_factory,
        "r1",
        [SourceName.PLAY_STORE],
        salt="s",
        connector_factory=factory_for(connectors),
    )
    report = quality_report(session_factory)
    play = report["sources"]["play_store"]
    assert play["items"] == 2 and play["raw_items"] == 2
    assert play["date_min"] == "2026-09-25" and play["date_max"] == "2026-09-30"
    assert play["missing"]["rating"] == 0.0 and play["missing"]["title"] == 1.0
    assert play["latest_run"]["status"] == "completed"
    assert any("below the Phase 1 target" in w for w in report["warnings"])
    md = render_markdown(report)
    assert "| play_store | 2 | 2 | 20,000 |" in md


# --- CLI ------------------------------------------------------------------------------

cli_runner = CliRunner()


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTHOR_HASH_SALT", "salt")

    def load_config_with_tmp_raw_dir():
        cfg = load_config()
        cfg.settings.ingest.raw_dir = str(tmp_path / "raw")
        return cfg

    monkeypatch.setattr(cli, "load_config", load_config_with_tmp_raw_dir)
    cli._context.cache_clear()
    yield url
    cli._context.cache_clear()


def test_cli_ingest_one_source(cli_db, monkeypatch):
    fake = FakeConnector(SourceName.PLAY_STORE, [play_raw("a", "x")])
    monkeypatch.setattr(runner, "build_connector", lambda source, ctx: fake)
    result = cli_runner.invoke(
        cli.app,
        [
            "ingest",
            "--source",
            "play_store",
            "--limit",
            "5",
            "--since",
            "2026-09-01",
            "--run-id",
            "cli-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[play_store] completed: fetched=1 new=1" in result.output
    assert fake.calls == [(datetime(2026, 9, 1, tzinfo=UTC), 5)]
    factory = make_session_factory(make_engine(cli_db))
    with session_scope(factory) as s:
        stages = {r.stage for r in s.scalars(select(PipelineRunRow))}
    assert stages == {"ingest", "ingest:play_store"}


def test_cli_ingest_requires_salt(cli_db, monkeypatch):
    monkeypatch.delenv("AUTHOR_HASH_SALT")
    result = cli_runner.invoke(cli.app, ["ingest", "--source", "app_store"])
    assert result.exit_code == 2 and "AUTHOR_HASH_SALT" in result.output


def test_cli_ingest_rejects_bad_since(cli_db):
    result = cli_runner.invoke(cli.app, ["ingest", "--since", "soon"])
    assert result.exit_code != 0


def test_cli_ingest_exit_code_when_every_source_fails(cli_db, monkeypatch):
    monkeypatch.setattr(
        runner,
        "build_connector",
        lambda source, ctx: FakeConnector(source, error=SourceError("down")),
    )
    result = cli_runner.invoke(cli.app, ["ingest"])
    assert result.exit_code == 1


def test_cli_quality_json(cli_db, tmp_path):
    out = tmp_path / "q.json"
    result = cli_runner.invoke(cli.app, ["quality", "--format", "json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["total_items"] == 0
