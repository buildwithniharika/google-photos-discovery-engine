"""Runs connectors: fetch -> raw store -> normalize -> items, one source at a time.

Each source gets its own `pipeline_runs` row (`ingest:{source}`) under the shared run id,
and a failure in one source never stops the others. Writes happen in batches, so a crash or
block mid-source keeps everything collected so far.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.config import AppConfig
from discovery.db import session_scope
from discovery.ingest.app_store import AppStoreConnector
from discovery.ingest.base import (
    RawStore,
    SourceConnector,
    SourceError,
    StoreResult,
    known_raw_ids,
)
from discovery.ingest.community import CommunityConnector
from discovery.ingest.google_sheet import GoogleSheetConnector
from discovery.ingest.http import HttpError, PoliteHttp, RobotsDisallowed
from discovery.ingest.play_store import PlayStoreConnector
from discovery.models.orm import ItemRow
from discovery.models.schemas import Item, RawItem, RunStatus, SourceName
from discovery.prep.normalize import SkipItem, normalize, upsert_items
from discovery.runs import StageRun, track_stage

log = logging.getLogger(__name__)

BATCH_SIZE = 500
MAX_LOGGED_WARNINGS = 50
ALL_SOURCES = (
    SourceName.PLAY_STORE,
    SourceName.APP_STORE,
    SourceName.GOOGLE_SHEET,
    SourceName.GOOGLE_COMMUNITY,
)


@dataclass
class IngestContext:
    cfg: AppConfig
    factory: sessionmaker[Session]
    run_id: str
    salt: str
    store: RawStore


ConnectorFactory = Callable[[SourceName, IngestContext], SourceConnector]


def build_connector(source: SourceName, ctx: IngestContext) -> SourceConnector:
    s = ctx.cfg.settings
    exception = s.ingest.robots_exceptions.get(source.value)
    common = {"run_id": ctx.run_id, "salt": ctx.salt}
    if source == SourceName.PLAY_STORE:
        http = PoliteHttp(s.http, robots_exception=exception)
        return PlayStoreConnector(s.sources.play_store, http=http, **common)
    if source == SourceName.APP_STORE:
        http = PoliteHttp(s.http, robots_exception=exception)
        return AppStoreConnector(s.sources.app_store, http=http, **common)
    if source == SourceName.GOOGLE_SHEET:
        http = PoliteHttp(s.http, robots_exception=exception)
        return GoogleSheetConnector(s.sources.google_sheet, http=http, **common)
    cs = s.sources.google_community
    http = PoliteHttp(s.http, robots_exception=exception, min_interval=cs.seconds_between_pages)
    run_dir = ctx.store.run_dir(source.value)
    return CommunityConnector(
        cs,
        http=http,
        known_ids=known_raw_ids(ctx.factory, source),
        debug_dir=run_dir / "debug" if run_dir else None,
        **common,
    )


def parse_since(value: str | None) -> datetime | None:
    """`YYYY-MM-DD` / ISO 8601 / `last` (resolved per source later)."""
    if value is None or value == "last":
        return None
    dt = datetime.fromisoformat(value)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def resolve_since(
    value: str | None, factory: sessionmaker[Session], source: SourceName, overlap_days: int
) -> datetime | None:
    """`last` = newest item date already stored for the source, minus an overlap window so
    late-arriving reviews are not missed (ING-PS-13)."""
    if value != "last":
        return parse_since(value)
    with session_scope(factory) as s:
        newest = s.scalar(
            select(func.max(ItemRow.date)).where(ItemRow.primary_source_name == source.value)
        )
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return newest - timedelta(days=overlap_days)


@dataclass
class SourceOutcome:
    source: SourceName
    status: RunStatus = RunStatus.COMPLETED
    counts: dict[str, Any] = field(default_factory=dict)


def _normalize_batch(
    batch: list[RawItem], dayfirst: bool, skipped: Counter[str]
) -> list[tuple[RawItem, Item]]:
    pairs = []
    for raw in batch:
        try:
            pairs.append((raw, normalize(raw, dayfirst=dayfirst)))
        except SkipItem as exc:
            skipped[exc.reason] += 1
    return pairs


def ingest_source(
    source: SourceName,
    ctx: IngestContext,
    *,
    since: str | None,
    limit: int | None,
    connector_factory: ConnectorFactory | None = None,
) -> SourceOutcome:
    s = ctx.cfg.settings
    outcome = SourceOutcome(source)
    with track_stage(ctx.factory, ctx.run_id, f"ingest:{source.value}") as run:
        resolved = resolve_since(since, ctx.factory, source, s.ingest.since_overlap_days)
        run.counts["since"] = resolved.isoformat() if resolved else None
        if exception := s.ingest.robots_exceptions.get(source.value):
            run.counts["robots_exception"] = exception
        connector = (connector_factory or build_connector)(source, ctx)
        raw = StoreResult()
        items: Counter[str] = Counter()
        skipped: Counter[str] = Counter()
        fetched = 0
        batch: list[RawItem] = []

        def flush() -> None:
            nonlocal fetched
            if not batch:
                return
            fetched += len(batch)
            raw.add(ctx.store.write(batch))
            pairs = _normalize_batch(batch, s.sources.google_sheet.dayfirst, skipped)
            items.update(upsert_items(ctx.factory, ctx.run_id, pairs))
            batch.clear()
            log.info("[%s] %d fetched so far", source.value, fetched)

        try:
            for raw_item in connector.fetch(resolved, limit):
                batch.append(raw_item)
                if len(batch) >= BATCH_SIZE:
                    flush()
        except (SourceError, RobotsDisallowed, HttpError) as exc:
            run.error(str(exc), type=exc.__class__.__name__)
        finally:
            flush()
            _record(run, connector, fetched, raw, items, skipped)

        if fetched == 0:
            if not run.errors:
                run.error("source returned 0 items; previous data kept (ING-X-01)")
            else:
                run.final_status = RunStatus.FAILED
        outcome.counts = dict(run.counts)
    outcome.status = _status_of(run)
    return outcome


def _record(
    run: StageRun,
    connector: Any,
    fetched: int,
    raw: StoreResult,
    items: Counter[str],
    skipped: Counter[str],
) -> None:
    run.counts.update(
        fetched=fetched,
        raw_new=raw.new,
        raw_updated=raw.updated,
        raw_unchanged=raw.unchanged,
        items_new=items["new"],
        items_updated=items["updated"],
        items_unchanged=items["unchanged"],
        skipped=dict(skipped),
        **{f"stat_{k}": v for k, v in getattr(connector, "stats", {}).items()},
    )
    warnings = getattr(connector, "warnings", [])
    for w in warnings[:MAX_LOGGED_WARNINGS]:
        run.error(**w)
    if len(warnings) > MAX_LOGGED_WARNINGS:
        run.error(f"... {len(warnings) - MAX_LOGGED_WARNINGS} more warnings not logged")


def _status_of(run: StageRun) -> RunStatus:
    return run.final_status or (RunStatus.PARTIAL if run.errors else RunStatus.COMPLETED)


def run_ingest(
    cfg: AppConfig,
    factory: sessionmaker[Session],
    run_id: str,
    sources: Iterable[SourceName],
    *,
    since: str | None = None,
    limit: int | None = None,
    salt: str,
    connector_factory: ConnectorFactory | None = None,
) -> list[SourceOutcome]:
    ctx = IngestContext(
        cfg, factory, run_id, salt, RawStore(factory, run_id, cfg.settings.ingest.raw_path)
    )
    outcomes: list[SourceOutcome] = []
    with track_stage(factory, run_id, "ingest") as parent:
        for source in sources:
            try:
                outcome = ingest_source(
                    source, ctx, since=since, limit=limit, connector_factory=connector_factory
                )
            except Exception as exc:  # isolate unexpected failures; already logged as `failed`
                log.exception("Source %s crashed", source.value)
                outcome = SourceOutcome(source, RunStatus.FAILED, {"error": str(exc)})
            outcomes.append(outcome)
            parent.counts[source.value] = {
                "status": outcome.status.value,
                "fetched": outcome.counts.get("fetched", 0),
                "items_new": outcome.counts.get("items_new", 0),
            }
        statuses = {o.status for o in outcomes}
        if statuses == {RunStatus.FAILED}:
            parent.final_status = RunStatus.FAILED
        elif statuses != {RunStatus.COMPLETED}:
            parent.final_status = RunStatus.PARTIAL
    return outcomes
