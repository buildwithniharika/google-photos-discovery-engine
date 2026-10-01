"""Data Quality summary: counts per source and platform, date ranges, missing-field rates,
and volume against the Phase 1 targets (ING-X-05)."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from discovery.db import session_scope
from discovery.models.orm import ItemRow, PipelineRunRow, RawItemRow

# Phase 1 acceptance targets (implementation plan); adjust after Gate G1.
TARGETS = {"play_store": 20_000, "app_store": 2_000, "google_community": 1_000}
RATED_SOURCES = {"play_store", "app_store"}


def _iso(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def quality_report(factory: sessionmaker[Session]) -> dict[str, Any]:
    with session_scope(factory) as s:
        rows = s.execute(
            select(
                ItemRow.primary_source_name,
                ItemRow.platform,
                ItemRow.date,
                ItemRow.rating,
                ItemRow.title,
                ItemRow.author_hash,
                func.length(ItemRow.original_text),
            )
        ).all()
        raw_counts = dict(
            s.execute(
                select(RawItemRow.source_name, func.count()).group_by(RawItemRow.source_name)
            ).all()
        )
        latest_runs = {
            r.stage.split(":", 1)[1]: r
            for r in s.scalars(
                select(PipelineRunRow)
                .where(PipelineRunRow.stage.like("ingest:%"))
                .order_by(PipelineRunRow.started_at)
            )
        }
        runs = {
            src: {
                "run_id": r.run_id,
                "status": r.status,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "errors": len(r.errors),
            }
            for src, r in latest_runs.items()
        }

    by_source: dict[str, list] = defaultdict(list)
    for row in rows:
        by_source[row[0]].append(row)

    sources: dict[str, Any] = {}
    for src in sorted(set(by_source) | set(raw_counts)):
        items = by_source.get(src, [])
        n = len(items)
        dates = [r[2] for r in items if r[2] is not None]

        def missing(idx: int, rows: list = items, n: int = n) -> float | None:
            return round(sum(r[idx] is None for r in rows) / n, 3) if n else None

        sources[src] = {
            "raw_items": raw_counts.get(src, 0),
            "items": n,
            "platforms": dict(Counter(r[1] for r in items).most_common()),
            "date_min": _iso(min(dates)) if dates else None,
            "date_max": _iso(max(dates)) if dates else None,
            "missing": {
                "date": missing(2),
                "rating": missing(3) if src in RATED_SOURCES else None,
                "title": missing(4),
                "author_hash": missing(5),
            },
            "median_text_chars": int(statistics.median(r[6] for r in items)) if n else 0,
            "target": TARGETS.get(src),
            "latest_run": runs.get(src),
        }

    warnings = []
    for src, target in TARGETS.items():
        got = sources.get(src, {}).get("items", 0)
        if got < target:
            warnings.append(f"{src}: {got:,} items, below the Phase 1 target of {target:,}")
    for src, info in sources.items():
        if info["latest_run"] and info["latest_run"]["status"] not in ("completed",):
            warnings.append(f"{src}: latest ingest run is {info['latest_run']['status']}")

    return {
        "total_items": len(rows),
        "platforms": dict(Counter(r[1] for r in rows).most_common()),
        "sources": sources,
        "warnings": warnings,
    }


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Data Quality summary",
        "",
        f"Total items: {report['total_items']:,}",
        "",
        "| Source | Raw | Items | Target | Date range | Missing date | Missing rating "
        "| Missing title | Median chars | Latest run |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for src, s in report["sources"].items():
        run = s["latest_run"]
        target = f"{s['target']:,}" if s["target"] else "-"
        lines.append(
            f"| {src} | {s['raw_items']:,} | {s['items']:,} | {target} | "
            f"{s['date_min'] or '?'} to {s['date_max'] or '?'} | {_pct(s['missing']['date'])} | "
            f"{_pct(s['missing']['rating'])} | {_pct(s['missing']['title'])} | "
            f"{s['median_text_chars']:,} | {run['status'] if run else '-'} |"
        )
    lines += ["", "| Platform | Items |", "|---|---:|"]
    lines += [f"| {p} | {n:,} |" for p, n in report["platforms"].items()]
    if report["warnings"]:
        lines += ["", "**Warnings**", ""] + [f"- {w}" for w in report["warnings"]]
    return "\n".join(lines) + "\n"


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
