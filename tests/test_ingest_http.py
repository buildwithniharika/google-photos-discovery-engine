from __future__ import annotations

import httpx
import pytest

from discovery.ingest.http import HttpError, Pacer, RobotsDisallowed, RobotsRules
from tests.ingest_helpers import make_http

PLAY_ROBOTS = """
User-Agent: *
Allow: /store/devices
Disallow: /store/people/details
Disallow: /_
Disallow: /store/apps/collection/p3_details_*
"""

ITUNES_ROBOTS = """
User-agent: *
Disallow: /WebObjects/*
Allow: /WebObjects/MZStore.woa/wa/viewMultiRoom?*
Disallow: /*/rss/*

User-agent: Googlebot
Disallow: /*/album/*/*?i=*
"""

DOCS_ROBOTS = """
User-agent: *
Crawl-delay: 1
Allow: /$
Allow: /spreadsheet
Disallow: /
"""

SUPPORT_ROBOTS = """
User-Agent: *
Disallow: /*/search
Disallow: /*/apis
Disallow: /*/api
Disallow: /*/forum-attachment
"""


@pytest.mark.parametrize(
    ("robots", "url", "allowed"),
    [
        (PLAY_ROBOTS, "https://play.google.com/_/PlayStoreUi/data/batchexecute", False),
        (PLAY_ROBOTS, "https://play.google.com/store/apps/details?id=x", True),
        (ITUNES_ROBOTS, "https://itunes.apple.com/us/rss/customerreviews/page=1/id=1/json", False),
        (ITUNES_ROBOTS, "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewMultiRoom?a", True),
        (ITUNES_ROBOTS, "https://itunes.apple.com/WebObjects/other", False),
        (DOCS_ROBOTS, "https://docs.google.com/spreadsheets/d/abc/export?format=csv", True),
        (DOCS_ROBOTS, "https://docs.google.com/", True),
        (DOCS_ROBOTS, "https://docs.google.com/uc?id=1", False),
        (SUPPORT_ROBOTS, "https://support.google.com/photos/thread/123/title?hl=en", True),
        (SUPPORT_ROBOTS, "https://support.google.com/photos/threads?hl=en&max_results=200", True),
        (SUPPORT_ROBOTS, "https://support.google.com/photos/search?q=x", False),
        (SUPPORT_ROBOTS, "https://support.google.com/apis/render?hl=en", True),
    ],
)
def test_robots_rules_follow_rfc9309(robots, url, allowed):
    assert RobotsRules.parse(robots, "discovery-research-bot/0.1").allowed(url) is allowed


def test_robots_specific_group_replaces_wildcard_group():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: discovery-research-bot\nAllow: /\n"
    assert RobotsRules.parse(text, "discovery-research-bot/0.1 (+url)").allowed("https://x/a")
    assert not RobotsRules.parse(text, "otherbot/1.0").allowed("https://x/a")


def test_robots_end_anchor():
    rules = RobotsRules.parse("User-agent: *\nDisallow: /*.pdf$\n", "bot")
    assert not rules.allowed("https://x/file.pdf")
    assert rules.allowed("https://x/file.pdf?download=1")


def test_get_raises_when_robots_disallows():
    http = make_http(lambda r: httpx.Response(200), robots="User-agent: *\nDisallow: /private")
    with pytest.raises(RobotsDisallowed):
        http.get("https://example.com/private/page")


def test_recorded_exception_allows_disallowed_url():
    http = make_http(
        lambda r: httpx.Response(200, text="ok"),
        robots="User-agent: *\nDisallow: /_",
        exception="PM decision",
    )
    assert http.get("https://example.com/_/data").text == "ok"
    assert http.exceptions_used == {"example.com"}


def test_missing_robots_allows_but_server_error_disallows():
    def handler_404(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200, text="ok")

    def handler_503(request):
        return httpx.Response(503 if request.url.path == "/robots.txt" else 200, text="ok")

    from discovery.ingest.http import PoliteHttp
    from tests.ingest_helpers import HTTP_SETTINGS

    ok = PoliteHttp(HTTP_SETTINGS, transport=httpx.MockTransport(handler_404), sleep=lambda s: None)
    assert ok.get("https://a.example/x").text == "ok"
    blocked = PoliteHttp(
        HTTP_SETTINGS, transport=httpx.MockTransport(handler_503), sleep=lambda s: None
    )
    with pytest.raises(RobotsDisallowed):
        blocked.get("https://b.example/x")


def test_retries_5xx_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) < 3 else 200, text="done")

    sleeps: list[float] = []
    http = make_http(handler, sleeps=sleeps)
    assert http.get("https://example.com/x").text == "done"
    assert len(calls) == 3
    assert len([s for s in sleeps if s > 0]) == 2  # backoff between attempts


def test_429_honors_retry_after():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, text="ok")

    sleeps: list[float] = []
    http = make_http(handler, sleeps=sleeps)
    assert http.get("https://example.com/x").text == "ok"
    assert 7.0 in sleeps


def test_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    http = make_http(handler)
    with pytest.raises(HttpError) as err:
        http.get("https://example.com/missing")
    assert err.value.status == 404 and len(calls) == 1


def test_identifying_user_agent_is_sent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200)

    make_http(handler).get("https://example.com/x")
    assert seen["ua"] == "discovery-test/0.1"


def test_pacer_spaces_requests_per_host():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    pacer = Pacer(2.5, 0.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        pacer.wait("support.google.com")
    pacer.wait("other.host")
    assert sleeps == [2.5, 2.5]
