"""Connector interface, shared helpers, and the raw store (architecture Section 5).

Raw payloads are written before any processing:
- `raw_items` table (SQLite locally, Postgres when deployed) holds the latest version of each
  payload, keyed by `raw_id`; re-runs upsert, so nothing is ever deleted (ING-X-01).
- `data/raw/{source}/{run_id}/items.jsonl` is an append-only snapshot of every run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.db import session_scope
from discovery.models.orm import RawItemRow
from discovery.models.schemas import Platform, RawItem, SourceName

# Placeholder names that identify nobody; hashing them would merge unrelated authors.
GENERIC_AUTHORS = frozenset({"a google user", "anonymous", "[deleted]", "deleted"})


class SourceError(Exception):
    """A source failed; the other sources keep running."""


class SourceBlocked(SourceError):
    """CAPTCHA or bot block. Stop politely; never try to bypass (ING-GC-04)."""


class SourceConnector(Protocol):
    source_name: SourceName
    platform: Platform
    warnings: list[dict[str, Any]]

    def fetch(self, since: datetime | None, limit: int | None) -> Iterator[RawItem]: ...


def hash_author(name: str | None, salt: str) -> str | None:
    if not name or name.strip().lower() in GENERIC_AUTHORS:
        return None
    digest = hashlib.sha256(f"{salt}:{name.strip().lower()}".encode()).hexdigest()
    return digest[:16]


def to_utc(dt: datetime) -> datetime:
    """Aware datetimes are converted; naive ones are assumed to be UTC already."""
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    return to_utc(dt).isoformat() if dt else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _content(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "_meta"}


def _stored_payload(item: RawItem) -> dict[str, Any]:
    meta = {**item.payload.get("_meta", {}), "platform": item.platform.value}
    meta["source_url"] = item.source_url
    return {**item.payload, "_meta": meta}


@dataclass
class StoreResult:
    new: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, other: StoreResult) -> None:
        self.new += other.new
        self.updated += other.updated
        self.unchanged += other.unchanged


@dataclass
class RawStore:
    factory: sessionmaker[Session]
    run_id: str
    jsonl_root: Path | None = None
    _files: dict[str, Path] = field(default_factory=dict)

    def run_dir(self, source: str) -> Path | None:
        if self.jsonl_root is None:
            return None
        path = self.jsonl_root / source / self.run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, items: Sequence[RawItem]) -> StoreResult:
        result = StoreResult()
        if not items:
            return result
        self._append_jsonl(items)
        by_id = {it.raw_id: it for it in items}
        with session_scope(self.factory) as s:
            existing = {
                row.raw_id: row
                for row in s.scalars(select(RawItemRow).where(RawItemRow.raw_id.in_(list(by_id))))
            }
            for raw_id, item in by_id.items():
                row = existing.get(raw_id)
                payload = _stored_payload(item)
                if row is None:
                    s.add(
                        RawItemRow(
                            raw_id=raw_id,
                            source_name=item.source_name.value,
                            run_id=item.run_id,
                            fetched_at=item.fetched_at,
                            payload=payload,
                        )
                    )
                    result.new += 1
                elif _content(row.payload) != _content(payload):
                    row.payload = payload
                    row.run_id = item.run_id
                    row.fetched_at = item.fetched_at
                    result.updated += 1
                else:
                    if row.payload.get("_meta") != payload["_meta"]:
                        row.payload = payload
                    result.unchanged += 1
        return result

    def _append_jsonl(self, items: Sequence[RawItem]) -> None:
        if self.jsonl_root is None:
            return
        source = items[0].source_name.value
        path = self._files.get(source)
        if path is None:
            run_dir = self.run_dir(source)
            assert run_dir is not None
            path = self._files[source] = run_dir / "items.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it.model_dump(mode="json"), ensure_ascii=False) + "\n")


def known_raw_ids(factory: sessionmaker[Session], source: SourceName) -> set[str]:
    with session_scope(factory) as s:
        return set(
            s.scalars(select(RawItemRow.raw_id).where(RawItemRow.source_name == source.value))
        )


def load_raw_items(
    factory: sessionmaker[Session], source: SourceName, raw_ids: Sequence[str] | None = None
) -> list[RawItem]:
    with session_scope(factory) as s:
        q = select(RawItemRow).where(RawItemRow.source_name == source.value)
        if raw_ids is not None:
            q = q.where(RawItemRow.raw_id.in_(list(raw_ids)))
        return [
            RawItem(
                raw_id=r.raw_id,
                source_name=SourceName(r.source_name),
                platform=Platform(r.payload["_meta"]["platform"]),
                source_url=r.payload["_meta"]["source_url"],
                fetched_at=r.fetched_at,
                run_id=r.run_id,
                payload=r.payload,
            )
            for r in s.scalars(q).all()
        ]
