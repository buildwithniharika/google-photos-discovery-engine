from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from discovery.config import (
    AppStoreSettings,
    GoogleCommunitySettings,
    GoogleSheetSettings,
    PlayStoreSettings,
)
from discovery.ingest.app_store import AppStoreConnector, parse_app_store_html, rss_entries
from discovery.ingest.base import SourceBlocked, SourceError, hash_author
from discovery.ingest.browser import PageUnavailable, looks_blocked
from discovery.ingest.community import (
    CommunityConnector,
    parse_post_date,
    parse_thread,
    parse_thread_list,
)
from discovery.ingest.google_sheet import (
    GoogleSheetConnector,
    detect_header,
    discover_tabs,
    map_columns,
    sheet_platform,
)
from discovery.ingest.play_store import PlayStoreConnector
from discovery.models.schemas import Platform
from tests.ingest_helpers import fixture, make_http

SALT = "test-salt"

# --- Play Store ------------------------------------------------------------------------


def _review(rid: str, at: datetime, **extra) -> dict:
    return {
        "reviewId": rid,
        "userName": "Real Person",
        "userImage": "https://img",
        "content": f"text {rid}",
        "score": 2,
        "thumbsUpCount": 3,
        "reviewCreatedVersion": "7.1",
        "appVersion": "7.1",
        "at": at,
        "replyContent": None,
        "repliedAt": None,
        **extra,
    }


class FakePlay:
    """Pages of reviews per country; tokens are opaque objects with a `.token`."""

    def __init__(self, pages: dict[str, list[list[dict]]], fail: set[str] = frozenset()):
        self.pages = pages
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, app_id, *, count, continuation_token, lang=None, country=None):
        self.calls.append({"country": country, "count": count, "token": continuation_token})
        if continuation_token is None:
            if country in self.fail:
                raise OSError("HTTP Error 503")
            state = SimpleNamespace(country=country, page=0)
        else:
            state = continuation_token
            state.page += 1
        pages = self.pages.get(state.country, [])
        batch = pages[state.page] if state.page < len(pages) else []
        state.token = "more" if state.page + 1 < len(pages) else None
        return batch[:count], state


def _play(fetch, countries=("in", "us"), per_country=100, http=None) -> PlayStoreConnector:
    settings = PlayStoreSettings(
        app_id="com.google.android.apps.photos",
        countries=list(countries),
        max_reviews_per_country=per_country,
        requests_per_second=1000,
        batch_size=2,
    )
    http = http or make_http(lambda r: httpx.Response(200), exception="recorded")
    return PlayStoreConnector(
        settings, run_id="r1", salt=SALT, http=http, fetch_page=fetch, sleep=lambda s: None
    )


T0 = datetime(2026, 9, 30, 12, tzinfo=UTC)


def test_play_paginates_dedups_countries_and_hashes_authors():
    fetch = FakePlay(
        {
            "in": [[_review("a", T0), _review("b", T0)], [_review("c", T0 - timedelta(days=1))]],
            "us": [[_review("a", T0), _review("d", T0)]],
        }
    )
    connector = _play(fetch)
    items = list(connector.fetch(None, None))
    ids = [i.raw_id for i in items]
    assert ids == ["play_store:a", "play_store:b", "play_store:c", "play_store:a", "play_store:d"]
    last_a = [i for i in items if i.raw_id == "play_store:a"][-1]
    assert last_a.payload["_meta"]["countries"] == ["in", "us"]  # ING-PS-01
    assert connector.stats["duplicates_across_countries"] == 1
    payload = items[0].payload
    assert "userName" not in payload and "userImage" not in payload
    assert payload["author_hash"] == hash_author("Real Person", SALT)
    assert payload["at"] == T0.isoformat()
    assert items[0].source_url.endswith("&reviewId=a")
    assert fetch.calls[1]["token"] is not None  # second page used the continuation token


def test_play_stops_at_since():
    fetch = FakePlay(
        {"in": [[_review("new", T0), _review("old", T0 - timedelta(days=30))], [_review("x", T0)]]}
    )
    items = list(_play(fetch, countries=["in"]).fetch(T0 - timedelta(days=3), None))
    assert [i.raw_id for i in items] == ["play_store:new"]
    assert len(fetch.calls) == 1  # did not request the next page


def test_play_limit_counts_unique_reviews():
    fetch = FakePlay({"in": [[_review("a", T0), _review("b", T0)], [_review("c", T0)]]})
    items = list(_play(fetch, countries=["in"]).fetch(None, 2))
    assert [i.raw_id for i in items] == ["play_store:a", "play_store:b"]


def test_play_failed_country_is_a_warning_and_others_continue():
    fetch = FakePlay({"us": [[_review("d", T0)]]}, fail={"in"})
    connector = _play(fetch)
    items = list(connector.fetch(None, None))
    assert [i.raw_id for i in items] == ["play_store:d"]
    assert connector.warnings[0]["country"] == "in"


def test_play_every_country_failing_raises():
    with pytest.raises(SourceError, match="every country failed"):
        list(_play(FakePlay({}, fail={"in", "us"})).fetch(None, None))


def test_play_refuses_without_robots_exception():
    from discovery.ingest.http import RobotsDisallowed

    http = make_http(lambda r: httpx.Response(200), robots="User-Agent: *\nDisallow: /_")
    with pytest.raises(RobotsDisallowed):
        list(_play(FakePlay({}), http=http).fetch(None, None))


def test_play_keeps_developer_reply_out_of_content():
    review = _review("r", T0, replyContent="Hi, thanks", repliedAt=T0)
    items = list(_play(FakePlay({"in": [[review]]}), countries=["in"]).fetch(None, None))
    assert items[0].payload["content"] == "text r"
    assert items[0].payload["replyContent"] == "Hi, thanks"


# --- App Store -------------------------------------------------------------------------


def test_parse_app_store_page_merges_duplicate_cards():
    reviews = parse_app_store_html(fixture("app_store_page.html"))
    assert len(reviews) == 4
    first = reviews[0]
    assert first["id"] == "14580613165"
    assert first["rating"] == 5
    assert first["date"] == "2026-09-22T16:05:54.000Z"
    assert first["title"] and first["content"]


def _app_store(method="web", exception=None, robots="", handler=None, countries=("us", "gb")):
    settings = AppStoreSettings(
        app_id="962194608",
        app_slug="google-photos-backup-edit",
        countries=list(countries),
        method=method,
        pages=3,
    )
    http = make_http(handler, robots=robots, exception=exception)
    return AppStoreConnector(settings, run_id="r1", salt=SALT, http=http)


def test_app_store_web_fetches_each_storefront_and_hashes_authors():
    page = fixture("app_store_page.html")

    def handler(request):
        assert "see-all=reviews" in str(request.url)
        return httpx.Response(200, text=page)

    connector = _app_store(handler=handler)
    items = list(connector.fetch(None, None))
    assert len(items) == 4  # same review ids in both storefronts are kept once
    assert items[0].platform == Platform.IOS
    assert "author" not in items[0].payload
    assert items[0].payload["author_hash"] == hash_author("Reviewer 0", SALT)
    assert items[0].payload["_meta"]["via"] == "web"


def test_app_store_web_since_filters_old_reviews():
    page = fixture("app_store_page.html")
    items = list(
        _app_store(handler=lambda r: httpx.Response(200, text=page)).fetch(
            datetime(2026, 9, 1, tzinfo=UTC), None
        )
    )
    assert all(i.payload["date"] >= "2026-09-01" for i in items)


def test_rss_entries_handles_single_object_and_metadata_entry():
    assert len(rss_entries(json.loads(fixture("itunes_rss.json")))) == 2
    single = {"feed": {"entry": {"im:rating": {"label": "4"}, "content": {"label": "x"}}}}
    assert len(rss_entries(single)) == 1
    meta_only = {"feed": {"entry": [{"im:name": {"label": "Google Photos"}}]}}
    assert rss_entries(meta_only) == []


def test_app_store_rss_needs_a_robots_exception():
    connector = _app_store(
        method="rss",
        robots="User-agent: *\nDisallow: /*/rss/*",
        handler=lambda r: httpx.Response(200, json={}),
    )
    with pytest.raises(SourceError, match="robots_exceptions"):
        list(connector.fetch(None, None))


def test_app_store_rss_stops_at_first_empty_page():
    feed = json.loads(fixture("itunes_rss.json"))
    pages = []

    def handler(request):
        pages.append(str(request.url))
        if "page=1/" in str(request.url):
            return httpx.Response(200, json=feed)
        return httpx.Response(200, json={"feed": {}})

    connector = _app_store(method="rss", exception="recorded", handler=handler, countries=["us"])
    items = list(connector.fetch(None, None))
    assert len(items) == 2 and len(pages) == 2
    assert "author" not in items[0].payload
    assert items[0].payload["country"] == "us"


def test_app_store_one_failing_storefront_is_a_warning():
    page = fixture("app_store_page.html")

    def handler(request):
        if "/gb/" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    connector = _app_store(handler=handler)
    assert len(list(connector.fetch(None, None))) == 4
    assert connector.warnings[0]["country"] == "gb"


# --- Google Sheet ----------------------------------------------------------------------

HTMLVIEW = (
    '<script>var items=[{name: "relevant_reviews.csv", pageUrl: "https:\\/\\/docs.google.com'
    '\\/spreadsheets\\/d\\/S\\/htmlview\\/sheet?headers\\x3dtrue&gid=365974545", gid: "1"},'
    '{name: "Second \\x27tab\\x27", pageUrl: "https:\\/\\/x\\/sheet?headers\\x3dtrue&gid=42"}]'
    "</script>"
)


def test_discover_tabs():
    assert discover_tabs(HTMLVIEW) == [
        ("relevant_reviews.csv", "365974545"),
        ("Second 'tab'", "42"),
    ]


def test_detect_header_skips_preamble_rows():
    rows = [["Exported 2026"], [], ["source", "Content", "Date"], ["reddit", "hi", ""]]
    assert detect_header(rows) == 2
    with pytest.raises(SourceError, match="no text column"):
        detect_header([["a", "b"], ["c", "d"]])


def test_map_columns_with_overrides():
    header = ["source", "Body Text", "when"]
    assert map_columns(header, {"date": "when", "text": "Body Text"})["date"] == "when"
    with pytest.raises(SourceError, match="not in the sheet header"):
        map_columns(header, {"text": "missing"})


@pytest.mark.parametrize(
    ("value", "platform", "unknown"),
    [
        ("reddit", Platform.REDDIT, False),
        ("Reddit ", Platform.REDDIT, False),
        ("r/googlephotos", Platform.REDDIT, False),
        ("play_store", Platform.ANDROID, False),
        ("app_store", Platform.IOS, False),
        ("google_community", Platform.GOOGLE_COMMUNITY, False),
        ("youtube", Platform.YOUTUBE, False),
        ("forum_quora", Platform.WEB_FORUM, False),
        ("", Platform.REDDIT, False),
        ("tiktok", Platform.REDDIT, True),
    ],
)
def test_sheet_platform(value, platform, unknown):
    assert sheet_platform(value, Platform.REDDIT) == (platform, unknown)


def _sheet(csv_text: str, content_type: str = "text/csv", tabs: str = HTMLVIEW):
    def handler(request):
        if request.url.path.endswith("/htmlview"):
            return httpx.Response(200, text=tabs)
        if "gid=42" in str(request.url):
            return httpx.Response(200, text="", headers={"content-type": content_type})
        return httpx.Response(200, text=csv_text, headers={"content-type": content_type})

    settings = GoogleSheetSettings(sheet_id="S")
    return GoogleSheetConnector(settings, run_id="r1", salt=SALT, http=make_http(handler))


def test_sheet_reads_every_row_and_assigns_platforms():
    connector = _sheet(fixture("sheet.csv"))
    items = list(connector.fetch(None, None))
    assert len(items) == 17
    assert connector.stats["blank_rows"] == 1
    platforms = {i.payload["row"]["source"]: i.platform for i in items}
    assert platforms["reddit"] == Platform.REDDIT
    assert platforms["youtube"] == Platform.YOUTUBE
    assert platforms["forum_xda"] == Platform.WEB_FORUM
    assert platforms["play_store"] == Platform.ANDROID
    reddit = next(i for i in items if i.payload["row"]["source"] == "reddit")
    assert reddit.raw_id.startswith("google_sheet:365974545:")
    assert reddit.source_url.startswith("https://www.reddit.com/")
    assert "author" not in reddit.payload["row"]
    assert reddit.payload["author_hash"]
    assert reddit.payload["_meta"]["column_map"]["text"] == "content"


def test_sheet_login_page_fails_with_clear_message():
    connector = _sheet("<!DOCTYPE html><html>Sign in</html>", content_type="text/html")
    with pytest.raises(SourceError, match="every tab failed"):
        list(connector.fetch(None, None))
    assert "Anyone with the link" in connector.warnings[0]["message"]


def test_sheet_csv_with_embedded_newlines_and_commas():
    csv_text = 'source,content,date\nreddit,"line one\nline two, with comma",1789764622\n'
    items = list(_sheet(csv_text).fetch(None, None))
    assert items[0].payload["row"]["content"] == "line one\nline two, with comma"


def test_sheet_duplicate_ids_get_unique_raw_ids():
    csv_text = "id,content\nx1,first\nx1,second\n"
    ids = [i.raw_id for i in _sheet(csv_text).fetch(None, None)]
    assert len(set(ids)) == 2


# --- Help Community --------------------------------------------------------------------


def test_parse_thread_with_op_followup_and_expert_reply():
    t = parse_thread(fixture("community_thread_op_followup.html"), salt=SALT)
    assert t["title"] == "Recovering my old photos"
    assert t["body"].startswith("One day, I just saw that many of my old photos")
    assert t["same_question_count"] == 3291
    assert t["marked_duplicate"] is True
    assert t["state"] == ["Locked"]
    assert "photos_recovering" in t["details"]
    assert t["created_at"].startswith("2023-02-16")
    kinds = [(r["kind"], r["role"]) for r in t["replies"]]
    assert kinds == [("expert", "Platinum Product Expert"), ("op_followup", None)]
    op = t["replies"][1]
    assert op["body"].startswith("But I myself didn't delete those photos")
    assert op["author_hash"] == t["author_hash"]  # the original poster


def test_parse_thread_dedups_highlighted_answer():
    t = parse_thread(fixture("community_thread_highlighted.html"), salt=SALT)
    assert t["title"] == "Data cannot restore from bin"
    assert len(t["replies"]) == 1
    assert t["replies"][0]["highlighted"] == "relevant answer"
    assert t["same_question_count"] == 0


def test_parse_thread_caps_replies_and_can_skip_them():
    html = fixture("community_thread_op_followup.html")
    capped = parse_thread(html, salt=SALT, max_replies=0)
    assert capped["replies"] == [] and capped["replies_truncated"] is True
    assert parse_thread(html, salt=SALT, include_replies=False)["replies"] == []


def test_parse_thread_returns_none_without_question():
    assert parse_thread("<html><body>Not found</body></html>", salt=SALT) is None


def test_parse_post_date_handles_narrow_nbsp():
    assert parse_post_date("2/16/2023, 11:14:50\u202fPM".replace("\u202f", " ")) == datetime(
        2023, 2, 16, 23, 14, 50, tzinfo=UTC
    )
    assert parse_post_date("Feb 16, 2023") == datetime(2023, 2, 16, tzinfo=UTC)
    assert parse_post_date("yesterday") is None


def test_parse_thread_list():
    refs = parse_thread_list(fixture("community_list.html"))
    assert len(refs) == 5
    assert refs[0]["thread_id"] == "470856683"
    assert refs[0]["url"].startswith("https://support.google.com/photos/thread/470856683/")
    assert refs[0]["counts"] == {
        "replies": 2,
        "upvotes": 2,
        "recommended_answers": 0,
        "relevant_answers": 1,
    }


def test_real_pages_are_not_mistaken_for_captcha():
    for name in ("community_thread_op_followup.html", "community_list.html"):
        assert not looks_blocked("https://support.google.com/photos", fixture(name))
    assert looks_blocked("https://www.google.com/sorry/index?continue=x", "")


class FakeBrowser:
    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.loaded: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def load(self, url, wait_for=None):
        tid = url.split("/thread/")[1].split("?")[0]
        self.loaded.append(tid)
        page = self.pages.get(tid)
        if isinstance(page, Exception):
            raise page
        return page


def _community(pages, known=frozenset(), tmp_path=None, per_run=10):
    settings = GoogleCommunitySettings(
        list_url="https://support.google.com/photos/threads?hl=en&thread_filter=(category:photos_restore)",
        list_page_size=20,
        max_threads=100,
        max_threads_per_run=per_run,
        seconds_between_pages=2.0,
    )
    list_html = fixture("community_list.html")
    http = make_http(lambda r: httpx.Response(200, text=list_html))
    browser = FakeBrowser(pages)
    connector = CommunityConnector(
        settings,
        run_id="r1",
        salt=SALT,
        http=http,
        known_ids={f"google_community:{k}" for k in known},
        debug_dir=tmp_path,
        browser_factory=lambda: browser,
    )
    return connector, browser


def test_community_skips_known_threads_and_continues_past_unavailable(tmp_path):
    good = fixture("community_thread_highlighted.html")
    refs = parse_thread_list(fixture("community_list.html"))
    ids = [r["thread_id"] for r in refs]
    pages = {ids[1]: PageUnavailable("404"), ids[2]: good, ids[3]: good, ids[4]: good}
    connector, browser = _community(pages, known={ids[0]}, tmp_path=tmp_path)
    items = list(connector.fetch(None, None))
    assert ids[0] not in browser.loaded  # resumable: already collected
    assert [i.raw_id for i in items] == [f"google_community:{t}" for t in ids[2:]]
    assert connector.stats["unavailable"] == 1
    assert connector.stats["already_collected"] == 1
    item = items[0]
    assert item.payload["title"] == "Data cannot restore from bin"
    assert item.payload["_meta"]["list_counts"]["replies"] is not None
    assert item.source_url == f"https://support.google.com/photos/thread/{ids[2]}?hl=en"


def test_community_respects_per_run_cap():
    good = fixture("community_thread_highlighted.html")
    ids = [r["thread_id"] for r in parse_thread_list(fixture("community_list.html"))]
    connector, browser = _community({t: good for t in ids}, per_run=2)
    assert len(list(connector.fetch(None, None))) == 2
    assert len(browser.loaded) == 2


def test_community_layout_change_fails_visibly(tmp_path):
    ids = [r["thread_id"] for r in parse_thread_list(fixture("community_list.html"))]
    connector, _ = _community({t: "<html>changed</html>" for t in ids}, tmp_path=tmp_path)
    with pytest.raises(SourceError, match="layout probably changed"):
        list(connector.fetch(None, None))
    assert any(tmp_path.glob("*.html"))  # raw HTML saved for debugging


def test_community_captcha_stops_the_source():
    ids = [r["thread_id"] for r in parse_thread_list(fixture("community_list.html"))]
    good = fixture("community_thread_highlighted.html")
    connector, browser = _community({ids[0]: good, ids[1]: SourceBlocked("captcha")})
    collected = []
    with pytest.raises(SourceBlocked):
        for item in connector.fetch(None, None):
            collected.append(item)
    assert len(collected) == 1 and len(browser.loaded) == 2
