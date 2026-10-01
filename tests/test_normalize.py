from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from discovery.db import session_scope
from discovery.models.orm import ItemRow, ItemSourceRow
from discovery.models.schemas import Item, Platform, RawItem, SourceName
from discovery.prep.normalize import (
    SkipItem,
    content_hash,
    item_id_for,
    normalize,
    parse_loose_date,
    parse_scraped_page,
    upsert_items,
)

REF = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)


def raw(source: SourceName, platform: Platform, payload: dict, raw_id: str = "x:1") -> RawItem:
    return RawItem(
        raw_id=raw_id,
        source_name=source,
        platform=platform,
        source_url="https://example.com/item",
        run_id="r1",
        payload=payload,
        fetched_at=REF,
    )


def play_payload(**over) -> dict:
    return {
        "reviewId": "gp1",
        "content": "Can't find my screenshots of a bill",
        "score": 1,
        "thumbsUpCount": 4,
        "reviewCreatedVersion": "7.92",
        "appVersion": "7.92",
        "at": "2026-09-30T10:00:00+00:00",
        "replyContent": "Hi, try search",
        "repliedAt": None,
        "author_hash": "abc",
        "_meta": {"countries": ["in", "us"]},
        **over,
    }


# --- dates ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected", "precision"),
    [
        ("1789764622", datetime(2026, 9, 18, 20, 50, 22, tzinfo=UTC), "exact"),
        ("1789764622000", datetime(2026, 9, 18, 20, 50, 22, tzinfo=UTC), "exact"),
        ("2026-09-17 20:16:04", datetime(2026, 9, 17, 20, 16, 4, tzinfo=UTC), "exact"),
        ("2026-08-25T19:42:25-07:00", datetime(2026, 8, 26, 2, 42, 25, tzinfo=UTC), "exact"),
        ("2026-08-25", datetime(2026, 8, 25, tzinfo=UTC), "day"),
        ("03/04/2025", datetime(2025, 4, 3, tzinfo=UTC), "day"),  # day-first sheet
        ("13/04/2025", datetime(2025, 4, 13, tzinfo=UTC), "day"),
        ("04/13/2025", datetime(2025, 4, 13, tzinfo=UTC), "day"),  # only valid month-first
        ("45000", datetime(2023, 3, 15, tzinfo=UTC), "day"),  # spreadsheet serial
        ("Feb 16, 2023", datetime(2023, 2, 16, tzinfo=UTC), "day"),
        ("9 months ago", REF - timedelta(days=270), "approximate"),
        ("a year ago (edited)", REF - timedelta(days=365), "approximate"),
        ("", None, None),
        ("#N/A", None, None),
        ("sometime", None, None),
    ],
)
def test_parse_loose_date(value, expected, precision):
    assert parse_loose_date(value, REF, dayfirst=True) == (expected, precision)


def test_parse_loose_date_month_first_setting():
    assert parse_loose_date("03/04/2025", REF, dayfirst=False)[0] == datetime(
        2025, 3, 4, tzinfo=UTC
    )


# --- per-source mappers ------------------------------------------------------------------


def test_play_mapper_keeps_developer_reply_separate():
    item = normalize(raw(SourceName.PLAY_STORE, Platform.ANDROID, play_payload()))
    assert item.original_text == "Can't find my screenshots of a bill"
    assert "Hi, try search" not in item.analysis_text
    assert item.metadata["developer_reply"]["text"] == "Hi, try search"
    assert item.metadata["countries"] == ["in", "us"]
    assert item.rating == 1 and item.engagement == {"thumbs_up": 4}
    assert item.date == datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert item.author_hash == "abc"
    assert item.item_id == item_id_for("x:1")
    assert item.content_hash == content_hash(item.analysis_text)


def test_rating_only_review_is_skipped():
    with pytest.raises(SkipItem, match="no_text"):
        normalize(raw(SourceName.PLAY_STORE, Platform.ANDROID, play_payload(content="  ")))


def test_future_date_is_nulled_and_flagged():
    item = normalize(
        raw(SourceName.PLAY_STORE, Platform.ANDROID, play_payload(at="2031-01-01T00:00:00+00:00"))
    )
    assert item.date is None and "future date" in item.metadata["date_flag"]


def test_app_store_web_and_rss_payloads():
    web = normalize(
        raw(
            SourceName.APP_STORE,
            Platform.IOS,
            {
                "id": "1",
                "title": "Search broken",
                "content": "Can't find it",
                "rating": 2,
                "date": "2026-09-22T16:05:54+00:00",
                "country": "us",
                "_meta": {"via": "web"},
            },
        )
    )
    assert web.analysis_text == "Search broken\n\nCan't find it"  # ING-AS-05
    assert web.metadata == {"review_id": "1", "country": "us", "via": "web"}
    rss = normalize(
        raw(
            SourceName.APP_STORE,
            Platform.IOS,
            {
                "id": {"label": "9"},
                "title": {"label": "T"},
                "content": {"label": "Body"},
                "im:rating": {"label": "4"},
                "im:version": {"label": "7.9"},
                "updated": {"label": "2026-09-29T19:01:40-07:00"},
                "im:voteSum": {"label": "3"},
                "country": "gb",
                "_meta": {"via": "rss"},
            },
        )
    )
    assert rss.rating == 4 and rss.metadata["app_version"] == "7.9"
    assert rss.engagement == {"vote_sum": 3}
    assert rss.date == datetime(2026, 9, 30, 2, 1, 40, tzinfo=UTC)


CMAP = {
    "text": "content",
    "title": "title",
    "date": "date",
    "url": "url",
    "source": "source",
    "id": "id",
    "author": "author",
    "rating": "rating",
    "score": "score",
    "comments": "comments",
    "subreddit": "subreddit",
    "snippet": "snippet",
}


def sheet_raw(row: dict, platform=Platform.REDDIT) -> RawItem:
    full = {k: "" for k in CMAP.values() if k != "author"} | {"matched_terms": "can't find"} | row
    return raw(
        SourceName.GOOGLE_SHEET,
        platform,
        {
            "row": full,
            "author_hash": "h1",
            "_meta": {"tab": "t", "gid": "1", "row_number": 5, "column_map": CMAP},
        },
    )


def test_sheet_reddit_row():
    item = normalize(
        sheet_raw(
            {
                "source": "reddit",
                "id": "1wk",
                "content": "Can't find a photo",
                "title": "Help",
                "date": "1789764622",
                "score": "6",
                "comments": "5",
                "subreddit": "r/googlephotos",
            }
        )
    )
    assert item.title == "Help" and item.analysis_text.startswith("Help\n\n")
    assert item.engagement == {"upvotes": 6, "comments": 5}
    assert item.rating is None  # Reddit has no rating
    assert item.metadata["subreddit"] == "r/googlephotos"
    assert item.metadata["sheet_columns"] == {"matched_terms": "can't find"}
    assert item.metadata["date_precision"] == "exact"


def test_sheet_play_row_keeps_rating():
    item = normalize(
        sheet_raw(
            {"source": "play_store", "content": "bad search", "rating": "1"}, Platform.ANDROID
        )
    )
    assert item.rating == 1


@pytest.mark.parametrize("text", ["[deleted]", "[removed]"])
def test_sheet_deleted_rows_are_skipped(text):
    with pytest.raises(SkipItem, match="deleted"):
        normalize(sheet_raw({"content": text}))


def test_sheet_formula_errors_are_null():
    item = normalize(sheet_raw({"content": "text", "date": "#REF!", "title": "#N/A"}))
    assert item.date is None and item.title is None


SCRAPED_OK = (
    "Title: Years after leaving Google Photos\n\nURL Source: https://www.xda.com/a/\n\n"
    "Published Time: 2026-09-09T23:00:15Z\n\nMarkdown Content:\nThe article body."
)
SCRAPED_403 = (
    "Title: 403 Forbidden\n\nURL Source: https://forums.example.com/t/1/\n\n"
    "Warning: Target URL returned error 403: Forbidden\n\nMarkdown Content:\n* * *\n\nnginx\n"
)


def test_parse_scraped_page():
    ok = parse_scraped_page(SCRAPED_OK)
    assert ok["body"] == "The article body." and ok["published"] == "2026-09-09T23:00:15Z"
    assert not ok["blocked"]
    assert parse_scraped_page(SCRAPED_403)["blocked"]
    assert parse_scraped_page("just a normal review") is None


def test_sheet_scraped_pages():
    ok = normalize(sheet_raw({"source": "forum_xda", "content": SCRAPED_OK}, Platform.WEB_FORUM))
    assert ok.original_text == "The article body."
    assert ok.title == "Years after leaving Google Photos"
    assert ok.date == datetime(2026, 9, 9, 23, 0, 15, tzinfo=UTC)
    assert ok.metadata["scraped_page"] is True
    blocked = normalize(
        sheet_raw(
            {
                "source": "forum_x",
                "content": SCRAPED_403,
                "title": "Can't find old photo",
                "snippet": "I can't find an old photo",
            },
            Platform.WEB_FORUM,
        )
    )
    assert blocked.metadata["fetch_blocked"] is True
    assert blocked.original_text == "I can't find an old photo"


def community_payload(**over) -> dict:
    return {
        "thread_id": "202250584",
        "url": "u",
        "title": "Recovering my old photos",
        "body": "My old photos disappeared",
        "created_at": "2023-02-16T23:14:50+00:00",
        "author_hash": "op",
        "same_question_count": 3291,
        "details": ["android"],
        "state": ["Locked"],
        "marked_duplicate": True,
        "replies_seen": 2,
        "replies_truncated": False,
        "replies": [
            {
                "kind": "expert",
                "role": "Platinum Product Expert",
                "author_hash": "e",
                "date": "2023-02-16T23:29:30+00:00",
                "body": "Check trash",
                "highlighted": None,
            },
            {
                "kind": "op_followup",
                "role": None,
                "author_hash": "op",
                "date": "2023-02-16T23:34:45+00:00",
                "body": "I didn't delete them",
                "highlighted": None,
            },
        ],
        "_meta": {
            "list_url": "https://support.google.com/photos/threads?thread_filter=(category:photos_restore)",
            "list_counts": {"replies": 2, "upvotes": 3291, "recommended_answers": 0},
        },
        **over,
    }


def test_community_mapper_question_is_unit_and_op_followups_join_analysis_text():
    item = normalize(
        raw(SourceName.GOOGLE_COMMUNITY, Platform.GOOGLE_COMMUNITY, community_payload())
    )
    assert item.original_text == "My old photos disappeared"
    assert item.analysis_text == (
        "Recovering my old photos\n\nMy old photos disappeared\n\n"
        "[Update from original poster] I didn't delete them"
    )
    assert "Check trash" not in item.analysis_text
    assert item.metadata["replies"][0]["kind"] == "expert"
    assert item.metadata["category"] == "photos_restore"
    assert item.engagement == {"same_question": 3291, "replies": 2, "recommended_answers": 0}


def test_community_title_only_thread():
    item = normalize(
        raw(
            SourceName.GOOGLE_COMMUNITY,
            Platform.GOOGLE_COMMUNITY,
            community_payload(body="", replies=[]),
        )
    )
    assert item.original_text == "Recovering my old photos"
    assert item.analysis_text == "Recovering my old photos"
    assert item.metadata["title_only"] is True


def test_analysis_text_does_not_repeat_title():
    item = Item(
        item_id="i",
        primary_source_name=SourceName.GOOGLE_SHEET,
        platform=Platform.REDDIT,
        source_url="u",
        title="Lost photo",
        original_text="Lost photo from Goa",
    )
    assert item.analysis_text == "Lost photo from Goa"


# --- persistence --------------------------------------------------------------------------


def test_upsert_items_tracks_history_on_edit(session_factory):
    r1 = raw(SourceName.PLAY_STORE, Platform.ANDROID, play_payload(), raw_id="play_store:gp1")
    first = normalize(r1)
    assert upsert_items(session_factory, "run1", [(r1, first)])["new"] == 1
    assert upsert_items(session_factory, "run2", [(r1, first)])["unchanged"] == 1

    with session_scope(session_factory) as s:
        s.get(ItemRow, first.item_id).clean_text = "processed"

    edited_raw = raw(
        SourceName.PLAY_STORE,
        Platform.ANDROID,
        play_payload(content="Found it via search", score=4),
        raw_id="play_store:gp1",
    )
    assert (
        upsert_items(session_factory, "run3", [(edited_raw, normalize(edited_raw))])["updated"] == 1
    )
    with session_scope(session_factory) as s:
        row = s.get(ItemRow, first.item_id)
        assert row.original_text == "Found it via search" and row.rating == 4
        assert row.clean_text is None  # Phase 2 re-processes it
        assert row.metadata_["history"][0]["original_text"] == "Can't find my screenshots of a bill"
        assert row.metadata_["history"][0]["rating"] == 1
        src = s.scalars(select(ItemSourceRow)).one()
        assert (src.raw_id, src.run_id) == ("play_store:gp1", "run3")
