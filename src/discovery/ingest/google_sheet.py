"""Google Sheet dataset via the public CSV export (architecture Section 5.3).

Tabs are discovered on every run from the sheet's `htmlview` page (ING-GS-02). Headers are
auto-detected and mapped to known fields; every original column is kept in the payload.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from discovery.config import GoogleSheetSettings
from discovery.ingest.base import SourceError, hash_author
from discovery.ingest.http import HttpError, PoliteHttp
from discovery.models.schemas import Platform, RawItem, SourceName

HTMLVIEW_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview"
CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
TAB_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"
_TAB = re.compile(r'\{name:\s*"((?:[^"\\]|\\.)*)",\s*pageUrl:\s*"[^"]*?gid(?:\\x3d|=)(\d+)')
HEADER_SCAN_ROWS = 10

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "text": ("content", "text", "body", "review", "review_text", "comment", "post", "message"),
    "title": ("title", "subject", "post_title"),
    "date": ("date", "created", "created_at", "created_utc", "timestamp", "posted_at", "at"),
    "url": ("url", "link", "permalink", "source_url"),
    "source": ("source", "platform", "site"),
    "id": ("id", "review_id", "post_id", "comment_id"),
    "author": ("author", "user", "username", "user_name", "reviewer"),
    "rating": ("rating", "stars", "star_rating"),
    "score": ("score", "upvotes", "ups", "likes"),
    "comments": ("comments", "num_comments"),
    "subreddit": ("subreddit",),
    "version": ("version", "app_version"),
    "snippet": ("snippet", "summary"),
    "video_url": ("video_url",),
}

PLATFORM_VALUES: dict[str, Platform] = {
    "reddit": Platform.REDDIT,
    "play_store": Platform.ANDROID,
    "play store": Platform.ANDROID,
    "google play": Platform.ANDROID,
    "android": Platform.ANDROID,
    "app_store": Platform.IOS,
    "app store": Platform.IOS,
    "ios": Platform.IOS,
    "google_community": Platform.GOOGLE_COMMUNITY,
    "google community": Platform.GOOGLE_COMMUNITY,
    "community": Platform.GOOGLE_COMMUNITY,
    "youtube": Platform.YOUTUBE,
}


def sheet_platform(value: str | None, default: Platform) -> tuple[Platform, bool]:
    """Platform for a row's source value; the flag is True when the value was unknown."""
    v = (value or "").strip().lower()
    if v in PLATFORM_VALUES:
        return PLATFORM_VALUES[v], False
    if v.startswith("r/"):
        return Platform.REDDIT, False
    if v.startswith("forum") or v in ("quora", "xda"):
        return Platform.WEB_FORUM, False
    return default, bool(v)


def discover_tabs(html: str) -> list[tuple[str, str]]:
    tabs = []
    for name, gid in _TAB.findall(html):
        decoded = json.loads('"' + name.replace("\\x", "\\u00").replace("\\/", "/") + '"')
        tabs.append((decoded, gid))
    return tabs


def _norm(header: str) -> str:
    return re.sub(r"\s+", "_", header.strip().lower())


def detect_header(rows: list[list[str]]) -> int:
    """Index of the first row that names a text column (ING-GS-03)."""
    text_aliases = set(FIELD_ALIASES["text"])
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if text_aliases & {_norm(c) for c in row}:
            return i
    raise SourceError(
        f"no text column found in the first {HEADER_SCAN_ROWS} rows "
        f"(expected one of: {', '.join(FIELD_ALIASES['text'])})"
    )


def map_columns(header: list[str], overrides: dict[str, str] | None) -> dict[str, str]:
    """{field: column name}. Config overrides win; unknown columns stay in the payload."""
    by_norm = {_norm(h): h for h in header if h.strip()}
    mapping: dict[str, str] = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in by_norm:
                mapping[field] = by_norm[alias]
                break
    for field, column in (overrides or {}).items():
        if column not in header:
            raise SourceError(f"column_map.{field}: column '{column}' not in the sheet header")
        mapping[field] = column
    if "text" not in mapping:
        raise SourceError("no text column mapped")
    return mapping


class GoogleSheetConnector:
    source_name = SourceName.GOOGLE_SHEET
    platform = Platform.REDDIT

    def __init__(self, settings: GoogleSheetSettings, *, run_id: str, salt: str, http: PoliteHttp):
        self.settings = settings
        self.run_id = run_id
        self.salt = salt
        self.http = http
        self.default_platform = Platform(settings.default_platform)
        self.warnings: list[dict[str, Any]] = []
        self.stats: dict[str, int] = {"blank_rows": 0, "unknown_platform_values": 0}

    def _url(self, template: str, **kw: str) -> str:
        return template.format(sheet_id=self.settings.sheet_id, **kw)

    def tabs(self) -> list[tuple[str, str]]:
        try:
            found = discover_tabs(self.http.get(self._url(HTMLVIEW_URL)).text)
        except HttpError as exc:
            raise SourceError(self._access_message(exc.status)) from exc
        if not found:
            self.warnings.append({"message": "could not discover tabs; using the first tab"})
            found = [("(first tab)", "0")]
        if self.settings.tabs == "auto":
            return found
        wanted = set(self.settings.tabs)
        missing = wanted - {name for name, _ in found}
        if missing:
            self.warnings.append({"message": f"configured tabs not found: {sorted(missing)}"})
        return [(n, g) for n, g in found if n in wanted]

    def fetch(self, since: datetime | None, limit: int | None) -> Iterator[RawItem]:
        # The sheet is a fixed dataset: every run reads it in full and the raw store's upsert
        # keeps re-runs idempotent, so `since` does not apply.
        tabs = self.tabs()
        self.stats["tabs"] = len(tabs)
        emitted = 0
        failed = 0
        for name, gid in tabs:
            try:
                for item in self._tab(name, gid):
                    if limit is not None and emitted >= limit:
                        return
                    emitted += 1
                    yield item
            except SourceError as exc:
                failed += 1
                self.warnings.append({"message": f"tab '{name}': {exc}", "tab": name})
        if tabs and failed == len(tabs):
            raise SourceError("Google Sheet: every tab failed")

    def _csv(self, gid: str) -> list[list[str]]:
        try:
            resp = self.http.get(self._url(CSV_URL, gid=gid))
        except HttpError as exc:
            raise SourceError(self._access_message(exc.status)) from exc
        body = resp.content
        if "text/html" in resp.headers.get("content-type", "") or body.lstrip()[:1] == b"<":
            raise SourceError(self._access_message(None))
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourceError(f"CSV export is not valid UTF-8: {exc}") from exc
        return list(csv.reader(io.StringIO(text, newline="")))

    def _access_message(self, status: int | None) -> str:
        got = f"HTTP {status}" if status else "a login page instead of CSV"
        return (
            f"Google Sheet {self.settings.sheet_id} returned {got}. Share it as 'Anyone with the "
            "link can view', or configure GOOGLE_SERVICE_ACCOUNT_FILE (decision D2)."
        )

    def _tab(self, name: str, gid: str) -> Iterator[RawItem]:
        rows = self._csv(gid)
        if not rows:
            return
        header_idx = detect_header(rows)
        header = rows[header_idx]
        overrides = self.settings.column_map if isinstance(self.settings.column_map, dict) else None
        cmap = map_columns(header, overrides)
        seen: set[str] = set()
        for offset, values in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            if not any(v.strip() for v in values):
                self.stats["blank_rows"] += 1
                continue
            row = {h: (values[i] if i < len(values) else "") for i, h in enumerate(header) if h}
            yield self._raw_item(name, gid, offset, row, cmap, seen)

    def _raw_item(
        self,
        tab: str,
        gid: str,
        row_number: int,
        row: dict[str, str],
        cmap: dict[str, str],
        seen: set[str],
    ) -> RawItem:
        def get(field: str) -> str:
            col = cmap.get(field)
            return row.get(col, "").strip() if col else ""

        native = get("id")
        if not native or len(native) > 200:
            basis = native or "|".join((get("text"), get("url"), get("date")))
            native = hashlib.sha256(basis.encode()).hexdigest()[:20]
        raw_id = f"google_sheet:{gid}:{native}"
        if raw_id in seen:
            raw_id = f"{raw_id}~{row_number}"
        seen.add(raw_id)

        platform, unknown = sheet_platform(get("source"), self.default_platform)
        if unknown:
            self.stats["unknown_platform_values"] += 1
        author_col = cmap.get("author")
        payload_row = {k: v for k, v in row.items() if k != author_col}
        payload = {
            "row": payload_row,
            "author_hash": hash_author(row.get(author_col) if author_col else None, self.salt),
            "_meta": {
                "tab": tab,
                "gid": gid,
                "row_number": row_number,
                "column_map": cmap,
                "platform_unknown_value": get("source") if unknown else None,
            },
        }
        return RawItem(
            raw_id=raw_id,
            source_name=self.source_name,
            platform=platform,
            source_url=self._source_url(get, platform, gid),
            run_id=self.run_id,
            payload=payload,
        )

    def _source_url(self, get, platform: Platform, gid: str) -> str:
        for candidate in (get("url"), get("video_url"), get("id")):
            if candidate.startswith("http"):
                return candidate
        if platform == Platform.ANDROID:
            return "https://play.google.com/store/apps/details?id=com.google.android.apps.photos&hl=en_IN"
        if platform == Platform.IOS:
            return "https://apps.apple.com/us/app/google-photos-backup-edit/id962194608?see-all=reviews"
        return self._url(TAB_URL, gid=gid)
