"""Per-source mappers from raw payloads to the unified `Item` schema (architecture 6.1).

Each raw item becomes one `items` row (plus an `item_sources` row). Cross-source duplicates
are merged later, in Phase 2 dedup. Dates are UTC; future dates are nulled and flagged
(ING-X-04). `original_text` is the user's text only: developer replies, thread replies, and
page-scrape wrappers are kept in `metadata`.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from discovery.db import session_scope
from discovery.ingest.base import parse_iso, to_utc
from discovery.models.orm import ItemRow, ItemSourceRow
from discovery.models.schemas import Item, Platform, RawItem, SourceName

FORMULA_ERRORS = frozenset(
    {"#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!", "#ERROR!"}
)
DELETED_TEXT = frozenset({"[deleted]", "[removed]"})
BLOCKED_PAGE_TITLES = ("403 forbidden", "just a moment", "access denied", "attention required")
HISTORY_LIMIT = 5

_RELATIVE = re.compile(
    r"(?P<n>\d+|an?|one)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago", re.I
)
_UNIT_DAYS = {
    "second": 1 / 86400,
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
_SLASH = re.compile(
    r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)
_NAMED_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y")
_SCRAPED = re.compile(
    r"^Title:\s*(?P<title>.*?)\n+URL Source:\s*(?P<url>\S+)\n+(?P<headers>.*?)"
    r"Markdown Content:\n?(?P<body>.*)$",
    re.S,
)
_CATEGORY = re.compile(r"category:([\w-]+)")


class SkipItem(Exception):
    """The raw item has no analyzable text; it stays in raw_items and is counted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- helpers -------------------------------------------------------------------


def item_id_for(raw_id: str) -> str:
    return hashlib.sha256(raw_id.encode()).hexdigest()[:32]


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()


def _rating(value: Any) -> int | None:
    try:
        r = int(float(value))
    except (TypeError, ValueError):
        return None
    return r if 1 <= r <= 5 else None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _engagement(**values: Any) -> dict[str, int]:
    return {k: n for k, v in values.items() if (n := _int(v)) is not None}


def parse_loose_date(
    value: str | None, reference: datetime, *, dayfirst: bool = True
) -> tuple[datetime | None, str | None]:
    """(UTC datetime, precision) for the formats seen in the sheet (ING-GS-05): epoch
    seconds/ms, ISO 8601, spreadsheet serials, DD/MM/YYYY or MM/DD/YYYY, named months, and
    relative dates ('9 months ago', resolved against `reference`, precision 'approximate')."""
    v = re.sub(r"\s*\(edited\)\s*$", "", (value or "").strip())
    if not v or v in FORMULA_ERRORS:
        return None, None
    if re.fullmatch(r"\d{10}(\.\d+)?", v):
        return datetime.fromtimestamp(float(v), UTC), "exact"
    if re.fullmatch(r"\d{13}", v):
        return datetime.fromtimestamp(int(v) / 1000, UTC), "exact"
    if re.fullmatch(r"\d{5}(\.\d+)?", v):
        return datetime(1899, 12, 30, tzinfo=UTC) + timedelta(days=float(v)), "day"
    lowered = v.lower()
    if lowered in ("today", "yesterday"):
        return to_utc(reference) - timedelta(days=lowered == "yesterday"), "approximate"
    if m := _RELATIVE.search(v):
        n = 1 if m["n"].lower() in ("a", "an", "one") else int(m["n"])
        return to_utc(reference) - timedelta(days=n * _UNIT_DAYS[m["unit"].lower()]), "approximate"
    try:
        return to_utc(datetime.fromisoformat(v.replace("Z", "+00:00"))), (
            "day" if len(v) == 10 else "exact"
        )
    except ValueError:
        pass
    if m := _SLASH.match(v):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        year += 2000 if year < 100 else 0
        day_first = a > 12 or (dayfirst and b <= 12)
        day, month = (a, b) if day_first else (b, a)
        try:
            dt = datetime(
                year, month, day, int(m[4] or 0), int(m[5] or 0), int(m[6] or 0), tzinfo=UTC
            )
        except ValueError:
            return None, None
        return dt, "exact" if m[4] else "day"
    for fmt in _NAMED_FORMATS:
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=UTC), "day"
        except ValueError:
            continue
    return None, None


def parse_scraped_page(text: str) -> dict[str, Any] | None:
    """Split a page-to-text capture ('Title: ...\\n\\nURL Source: ...\\n\\nMarkdown Content:')
    into its parts; `blocked` when the capture is an error page."""
    m = _SCRAPED.match(text)
    if not m:
        return None
    headers = dict(line.split(":", 1) for line in m["headers"].splitlines() if ":" in line)
    title = m["title"].strip()
    blocked = "Warning" in headers and "error" in headers["Warning"].lower()
    blocked = blocked or title.lower().startswith(BLOCKED_PAGE_TITLES)
    return {
        "title": title,
        "url": m["url"],
        "published": (headers.get("Published Time") or "").strip() or None,
        "body": m["body"].strip(),
        "blocked": blocked,
    }


# --- per-source mappers ----------------------------------------------------------
# Each returns the source-specific Item fields; `normalize` adds the common ones.


def _map_play(raw: RawItem, **_: Any) -> dict[str, Any]:
    p = raw.payload
    text = (p.get("content") or "").strip()
    if not text:
        raise SkipItem("no_text")
    meta: dict[str, Any] = {
        "review_id": p["reviewId"],
        "app_version": p.get("reviewCreatedVersion") or p.get("appVersion"),
        "countries": p.get("_meta", {}).get("countries", []),
    }
    if p.get("replyContent"):  # never part of the user's text (ING-PS-09)
        meta["developer_reply"] = {"text": p["replyContent"], "date": p.get("repliedAt")}
    return {
        "original_text": text,
        "date": parse_iso(p.get("at")),
        "rating": _rating(p.get("score")),
        "engagement": _engagement(thumbs_up=p.get("thumbsUpCount")),
        "metadata": meta,
    }


def _label(entry: dict[str, Any], key: str) -> str | None:
    v = entry.get(key)
    return v.get("label") if isinstance(v, dict) else v


def _map_app_store(raw: RawItem, **_: Any) -> dict[str, Any]:
    p = raw.payload
    if "im:rating" in p:  # RSS entry
        review_id, title, text = _label(p, "id"), _label(p, "title"), _label(p, "content")
        rating, date, version = (
            _label(p, "im:rating"),
            _label(p, "updated"),
            _label(p, "im:version"),
        )
        engagement = _engagement(
            vote_sum=_label(p, "im:voteSum"), vote_count=_label(p, "im:voteCount")
        )
    else:  # apps.apple.com page
        review_id, title, text = p["id"], p.get("title"), p.get("content")
        rating, date, version = p.get("rating"), p.get("date"), p.get("version")
        engagement = {}
    text = (text or "").strip()
    if not text:
        raise SkipItem("no_text")
    return {
        "title": (title or "").strip() or None,
        "original_text": text,
        "date": parse_iso(date),
        "rating": _rating(rating),
        "engagement": engagement,
        "metadata": {
            "review_id": review_id,
            "country": p.get("country"),
            "app_version": version,
            "via": p.get("_meta", {}).get("via"),
        },
    }


def _map_sheet(raw: RawItem, *, dayfirst: bool = True, **_: Any) -> dict[str, Any]:
    row: dict[str, str] = raw.payload["row"]
    meta_in = raw.payload["_meta"]
    cmap: dict[str, str] = meta_in["column_map"]

    def get(field: str) -> str:
        col = cmap.get(field)
        v = (row.get(col) or "").strip() if col else ""
        return "" if v in FORMULA_ERRORS else v

    text, title, snippet = get("text"), get("title") or None, get("snippet")
    meta: dict[str, Any] = {
        "sheet_tab": meta_in.get("tab"),
        "sheet_row": meta_in.get("row_number"),
        "sheet_source": get("source") or None,
        "sheet_id_value": get("id") or None,
    }
    if meta_in.get("platform_unknown_value"):
        meta["platform_unknown_value"] = meta_in["platform_unknown_value"]
    for field in ("subreddit", "version", "video_url", "snippet"):
        if value := get(field):
            meta[field] = value
    mapped = set(cmap.values())
    extra = {k: v.strip() for k, v in row.items() if k not in mapped and v.strip()}
    if extra:
        meta["sheet_columns"] = extra

    if text.lower() in DELETED_TEXT:
        raise SkipItem("deleted")
    published = None
    if scraped := parse_scraped_page(text):
        meta["scraped_page"] = True
        meta["page_title"] = scraped["title"]
        published = scraped["published"]
        title = title or (None if scraped["blocked"] else scraped["title"])
        if scraped["blocked"]:
            meta["fetch_blocked"] = True
            text = snippet or title or ""
        else:
            text = scraped["body"]
    if not text:
        raise SkipItem("no_text")

    date, precision = parse_loose_date(get("date") or published, raw.fetched_at, dayfirst=dayfirst)
    if precision:
        meta["date_precision"] = precision
    has_rating = raw.platform in (Platform.ANDROID, Platform.IOS)
    return {
        "title": title,
        "original_text": text,
        "date": date,
        "rating": _rating(get("rating")) if has_rating else None,
        "engagement": _engagement(upvotes=get("score") or None, comments=get("comments") or None),
        "metadata": meta,
    }


def _map_community(raw: RawItem, **_: Any) -> dict[str, Any]:
    p = raw.payload
    list_meta = p.get("_meta", {})
    counts = list_meta.get("list_counts") or {}
    title = (p.get("title") or "").strip()
    body = (p.get("body") or "").strip()
    if not (title or body):
        raise SkipItem("no_text")
    replies = p.get("replies", [])
    category = _CATEGORY.search(list_meta.get("list_url", ""))
    meta: dict[str, Any] = {
        "thread_id": p["thread_id"],
        "category": category.group(1) if category else None,
        "details": p.get("details", []),
        "state": p.get("state", []),
        "marked_duplicate": p.get("marked_duplicate", False),
        "op_followups": [r["body"] for r in replies if r["kind"] == "op_followup" and r["body"]],
        "replies": [
            {k: r.get(k) for k in ("kind", "role", "date", "highlighted", "body")}
            for r in replies
            if r["kind"] != "op_followup"
        ],
        "replies_truncated": p.get("replies_truncated", False),
    }
    if not body:
        meta["title_only"] = True  # ING-GC-05
    same = p.get("same_question_count")
    return {
        "title": title or None,
        "original_text": body or title,
        "date": parse_iso(p.get("created_at")),
        "rating": None,
        "engagement": _engagement(
            same_question=same if same is not None else counts.get("upvotes"),
            replies=counts.get("replies")
            if counts.get("replies") is not None
            else p.get("replies_seen"),
            recommended_answers=counts.get("recommended_answers"),
        ),
        "metadata": meta,
    }


MAPPERS: dict[SourceName, Callable[..., dict[str, Any]]] = {
    SourceName.PLAY_STORE: _map_play,
    SourceName.APP_STORE: _map_app_store,
    SourceName.GOOGLE_SHEET: _map_sheet,
    SourceName.GOOGLE_COMMUNITY: _map_community,
}


def normalize(raw: RawItem, *, now: datetime | None = None, dayfirst: bool = True) -> Item:
    """Raise `SkipItem` when the raw item has no analyzable text."""
    fields = MAPPERS[raw.source_name](raw, dayfirst=dayfirst)
    meta = fields.pop("metadata")
    date = fields.pop("date")
    if date is not None and date > (now or datetime.now(UTC)) + timedelta(days=1):
        meta["date_flag"] = f"future date nulled: {date.isoformat()}"
        date = None
    item = Item(
        item_id=item_id_for(raw.raw_id),
        primary_source_name=raw.source_name,
        platform=raw.platform,
        source_url=raw.source_url,
        date=date,
        author_hash=raw.payload.get("author_hash"),
        metadata={k: v for k, v in meta.items() if v not in (None, [], {})},
        **fields,
    )
    item.content_hash = content_hash(item.analysis_text)
    return item


# --- persistence -----------------------------------------------------------------


def upsert_items(
    factory: sessionmaker[Session], run_id: str, pairs: Sequence[tuple[RawItem, Item]]
) -> Counter[str]:
    """Insert new items; on changed content keep the previous text in `metadata.history` and
    clear Phase 2 fields so the item is re-processed (ING-PS-02, ING-GS-12)."""
    counts: Counter[str] = Counter()
    if not pairs:
        return counts
    latest = {item.item_id: (raw, item) for raw, item in pairs}
    now = datetime.now(UTC).isoformat()
    with session_scope(factory) as s:
        rows = {
            r.item_id: r
            for r in s.scalars(select(ItemRow).where(ItemRow.item_id.in_(list(latest))))
        }
        sources = {
            r.raw_id: r
            for r in s.scalars(
                select(ItemSourceRow).where(
                    ItemSourceRow.raw_id.in_([r.raw_id for r, _ in latest.values()])
                )
            )
        }
        for item_id, (raw, item) in latest.items():
            row = rows.get(item_id)
            if row is None:
                s.add(_new_row(item))
                counts["new"] += 1
            else:
                history = list(row.metadata_.get("history", []))
                if row.content_hash != item.content_hash:
                    history.append(
                        {
                            "original_text": row.original_text,
                            "title": row.title,
                            "rating": row.rating,
                            "replaced_at": now,
                        }
                    )
                    row.clean_text = None
                    row.language = None
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
                meta = dict(item.metadata)
                if history:
                    meta["history"] = history[-HISTORY_LIMIT:]
                _apply(row, item, meta)
            src = sources.get(raw.raw_id)
            if src is None:
                s.add(
                    ItemSourceRow(
                        raw_id=raw.raw_id,
                        item_id=item_id,
                        source_name=raw.source_name.value,
                        platform=raw.platform.value,
                        source_url=raw.source_url,
                        run_id=run_id,
                    )
                )
            else:
                src.run_id = run_id
                src.source_url = raw.source_url
                src.platform = raw.platform.value
    return counts


def _new_row(item: Item) -> ItemRow:
    row = ItemRow(item_id=item.item_id)
    _apply(row, item, item.metadata)
    return row


def _apply(row: ItemRow, item: Item, metadata: dict[str, Any]) -> None:
    row.primary_source_name = item.primary_source_name.value
    row.platform = item.platform.value
    row.source_url = item.source_url
    row.title = item.title
    row.original_text = item.original_text
    row.date = item.date
    row.rating = item.rating
    row.engagement = dict(item.engagement)
    row.author_hash = item.author_hash
    row.content_hash = item.content_hash
    row.metadata_ = metadata
