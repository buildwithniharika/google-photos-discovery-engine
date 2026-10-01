"""Apple App Store reviews (architecture Section 5.2).

Two methods, chosen by `sources.app_store.method`:
- `web` (default): the server-rendered apps.apple.com reviews page, allowed by robots.txt.
  It shows ~10 reviews per storefront, so the daily workflow accumulates them over time.
- `rss`: the iTunes RSS feed (up to 10 pages x 50 per storefront). robots.txt disallows
  `/*/rss/*`, so it only runs when `app_store` is listed in `ingest.robots_exceptions`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from discovery.config import AppStoreSettings
from discovery.ingest.base import SourceError, hash_author, iso, parse_iso
from discovery.ingest.http import HttpError, PoliteHttp, RobotsDisallowed
from discovery.models.schemas import Platform, RawItem, SourceName

RSS_URL = "https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
_REVIEW_ID = re.compile(r"review-(\d+)-title")
_DIGIT = re.compile(r"(\d)")


def parse_app_store_html(html: str) -> list[dict[str, Any]]:
    """Review cards from the apps.apple.com reviews page. Each review is rendered twice (card
    and modal), so cards are merged by id, keeping the longest text."""
    soup = BeautifulSoup(html, "html.parser")
    reviews: dict[str, dict[str, Any]] = {}
    for card in soup.select("[aria-labelledby^=review-]"):
        m = _REVIEW_ID.fullmatch(card.get("aria-labelledby", ""))
        if not m:
            continue
        stars = card.select_one("[aria-label$=Stars], [aria-label$=Star]")
        rating = _DIGIT.search(stars["aria-label"]) if stars else None
        when = card.select_one("time[datetime]")
        title = card.select_one("h3")
        author = card.select_one(".author")
        content = card.select_one(".content")
        review = {
            "id": m.group(1),
            "title": title.get_text(" ", strip=True) if title else None,
            "content": content.get_text("\n", strip=True) if content else "",
            "rating": int(rating.group(1)) if rating else None,
            "date": when["datetime"] if when else None,
            "author": author.get_text(strip=True) if author else None,
        }
        prev = reviews.get(review["id"])
        if prev is None or len(review["content"]) > len(prev["content"]):
            reviews[review["id"]] = review
    return list(reviews.values())


def rss_entries(feed: dict[str, Any]) -> list[dict[str, Any]]:
    """Review entries from one RSS page. A one-entry page is an object, not a list (ING-AS-04),
    and entries without a rating are app metadata, not reviews (ING-AS-01)."""
    entries = feed.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return [e for e in entries if "im:rating" in e and "content" in e]


class AppStoreConnector:
    source_name = SourceName.APP_STORE
    platform = Platform.IOS

    def __init__(self, settings: AppStoreSettings, *, run_id: str, salt: str, http: PoliteHttp):
        self.settings = settings
        self.run_id = run_id
        self.salt = salt
        self.http = http
        self.warnings: list[dict[str, Any]] = []
        self.stats: dict[str, int] = {}

    def page_url(self, country: str) -> str:
        s = self.settings
        return (
            f"https://apps.apple.com/{country}/app/{s.app_slug}/id{s.app_id}"
            "?see-all=reviews&platform=iphone"
        )

    def fetch(self, since: datetime | None, limit: int | None) -> Iterator[RawItem]:
        seen: set[str] = set()
        failed: list[str] = []
        per_country = self._web if self.settings.method == "web" else self._rss
        for country in self.settings.countries:
            try:
                for item in per_country(country, since):
                    if item.raw_id in seen:
                        continue
                    if limit is not None and len(seen) >= limit:
                        return
                    seen.add(item.raw_id)
                    yield item
            except RobotsDisallowed as exc:
                raise SourceError(
                    f"{exc}. The App Store RSS feed needs a recorded decision in "
                    "ingest.robots_exceptions.app_store, or use sources.app_store.method: web"
                ) from exc
            except (HttpError, SourceError) as exc:
                failed.append(country)
                self.warnings.append({"message": str(exc), "country": country})
        if failed and len(failed) == len(self.settings.countries):
            raise SourceError(f"App Store: every storefront failed ({', '.join(failed)})")

    def _web(self, country: str, since: datetime | None) -> Iterator[RawItem]:
        html = self.http.get(self.page_url(country)).text
        reviews = parse_app_store_html(html)
        self.stats[f"web_{country}"] = len(reviews)
        for r in reviews:
            when = parse_iso(r["date"])
            if since is not None and when is not None and when < since:
                continue
            author = r.pop("author")
            payload = {
                **r,
                "date": iso(when),
                "country": country,
                "author_hash": hash_author(author, self.salt),
                "_meta": {"via": "web"},
            }
            yield self._raw_item(r["id"], country, payload)

    def _rss(self, country: str, since: datetime | None) -> Iterator[RawItem]:
        for page in range(1, self.settings.pages + 1):
            url = RSS_URL.format(country=country, page=page, app_id=self.settings.app_id)
            entries = rss_entries(self.http.get(url).json())
            if not entries:
                return  # fewer than 10 pages is normal (ING-AS-02)
            all_older = True
            for e in entries:
                when = parse_iso(e.get("updated", {}).get("label"))
                if since is not None and when is not None and when < since:
                    continue
                all_older = False
                author = e.get("author", {}).get("name", {}).get("label")
                payload = {k: v for k, v in e.items() if k != "author"}
                payload |= {
                    "country": country,
                    "author_hash": hash_author(author, self.salt),
                    "_meta": {"via": "rss"},
                }
                yield self._raw_item(e["id"]["label"], country, payload)
            if since is not None and all_older:
                return

    def _raw_item(self, review_id: str, country: str, payload: dict[str, Any]) -> RawItem:
        return RawItem(
            raw_id=f"app_store:{review_id}",
            source_name=self.source_name,
            platform=self.platform,
            source_url=self.page_url(country),
            run_id=self.run_id,
            payload=payload,
        )
