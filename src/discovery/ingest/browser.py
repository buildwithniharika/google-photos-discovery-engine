"""Headless Chromium (Playwright) for JS-rendered pages, with the same robots.txt checks and
per-host pacing as `PoliteHttp`. Images, media, and fonts are not downloaded."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from discovery.ingest.base import SourceBlocked
from discovery.ingest.http import PoliteHttp

BLOCKED_RESOURCES = frozenset({"image", "media", "font"})
CAPTCHA_MARKERS = (
    "unusual traffic from your computer network",
    "our systems have detected unusual traffic",
    "g-recaptcha",
)


class PageUnavailable(Exception):
    """Deleted, private, or not rendered; skip it and continue (ING-GC-01)."""


def looks_blocked(url: str, html: str) -> bool:
    lowered = html[:200_000].lower()
    return "/sorry/" in url or any(m in lowered for m in CAPTCHA_MARKERS)


class BrowserSession:
    def __init__(self, http: PoliteHttp, *, timeout_ms: int = 30_000):
        self.http = http
        self.timeout_ms = timeout_ms
        self._pw: Any = None
        self._browser: Any = None
        self.page: Any = None

    def __enter__(self) -> BrowserSession:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        context = self._browser.new_context(
            user_agent=self.http.settings.user_agent, locale="en-US", timezone_id="UTC"
        )
        context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in BLOCKED_RESOURCES
                else route.continue_()
            ),
        )
        self.page = context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def load(self, url: str, wait_for: str | None = None) -> str:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        self.http.check_robots(url)
        self.http.pacer.wait(urlsplit(url).netloc)
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except PlaywrightTimeout as exc:
            raise PageUnavailable(f"timed out loading {url}") from exc
        status = resp.status if resp else None
        if status == 429:
            raise SourceBlocked(f"HTTP 429 from {url}; stopping politely")
        if status in (403, 404, 410):
            if looks_blocked(self.page.url, self.page.content()):
                raise SourceBlocked(f"bot check at {self.page.url}; stopping politely")
            raise PageUnavailable(f"HTTP {status} for {url}")
        if wait_for:
            try:
                self.page.wait_for_selector(wait_for, timeout=self.timeout_ms)
            except PlaywrightTimeout as exc:
                html = self.page.content()
                if looks_blocked(self.page.url, html):
                    raise SourceBlocked(f"bot check at {self.page.url}; stopping politely") from exc
                raise PageUnavailable(f"content did not render for {url}") from exc
        html = self.page.content()
        if looks_blocked(self.page.url, html):
            raise SourceBlocked(f"bot check at {self.page.url}; stopping politely")
        return html
