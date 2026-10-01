"""Google Play Store reviews via `google-play-scraper` (architecture Section 5.1)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from discovery.config import PlayStoreSettings
from discovery.ingest.base import SourceError, hash_author, iso
from discovery.ingest.http import PoliteHttp, polite_pause
from discovery.models.schemas import Platform, RawItem, SourceName

log = logging.getLogger(__name__)

# The endpoint google-play-scraper posts to; robots.txt disallows it ('Disallow: /_').
REVIEWS_ENDPOINT = "https://play.google.com/_/PlayStoreUi/data/batchexecute"

FetchPage = Callable[..., tuple[list[dict[str, Any]], Any]]


def _library_fetch(app_id: str, **kwargs: Any) -> tuple[list[dict[str, Any]], Any]:
    from google_play_scraper import Sort, reviews

    return reviews(app_id, sort=Sort.NEWEST, **kwargs)


def _transient(exc: BaseException) -> bool:
    return not isinstance(exc, TypeError | AttributeError | NameError)


def _utc(value: datetime | None) -> datetime | None:
    # The library builds naive datetimes with `datetime.fromtimestamp` (local time), so a naive
    # value is converted from local time; an aware value is just converted.
    return value.astimezone(UTC) if value else None


class PlayStoreConnector:
    source_name = SourceName.PLAY_STORE
    platform = Platform.ANDROID

    def __init__(
        self,
        settings: PlayStoreSettings,
        *,
        run_id: str,
        salt: str,
        http: PoliteHttp,
        fetch_page: FetchPage = _library_fetch,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ):
        self.settings = settings
        self.run_id = run_id
        self.salt = salt
        self.http = http
        self._fetch_page = fetch_page
        self._sleep = sleep
        self._max_attempts = max_attempts
        self.warnings: list[dict[str, Any]] = []
        self.stats: dict[str, int] = {"duplicates_across_countries": 0}

    def app_url(self, review_id: str | None = None) -> str:
        url = f"https://play.google.com/store/apps/details?id={self.settings.app_id}&hl=en_IN"
        return f"{url}&reviewId={review_id}" if review_id else url

    def fetch(self, since: datetime | None, limit: int | None) -> Iterator[RawItem]:
        self.http.check_robots(REVIEWS_ENDPOINT)
        countries: dict[str, list[str]] = {}
        failed: list[str] = []
        for country in self.settings.countries:
            if limit is not None and len(countries) >= limit:
                break
            try:
                for review in self._country(country, since):
                    rid = review["reviewId"]
                    if rid in countries:
                        countries[rid].append(country)
                        self.stats["duplicates_across_countries"] += 1
                    elif limit is not None and len(countries) >= limit:
                        break
                    else:
                        countries[rid] = [country]
                    yield self._raw_item(review, countries[rid])
            except SourceError as exc:
                failed.append(country)
                self.warnings.append({"message": str(exc), "country": country})
        if failed and len(failed) == len(self.settings.countries):
            raise SourceError(f"Play Store: every country failed ({', '.join(failed)})")

    def _country(self, country: str, since: datetime | None) -> Iterator[dict[str, Any]]:
        token = None
        got = 0
        cap = self.settings.max_reviews_per_country
        while got < cap:
            count = min(self.settings.batch_size, cap - got)
            batch, token = self._call(country, count, token)
            if not batch:
                return
            reached_since = False
            for review in batch:
                at = _utc(review.get("at"))
                if since is not None and at is not None and at < since:
                    reached_since = True
                    continue
                got += 1
                yield review
            if reached_since or token is None or getattr(token, "token", None) is None:
                return
            polite_pause(1.0 / self.settings.requests_per_second, 0.5, self._sleep)

    def _call(self, country: str, count: int, token: Any) -> tuple[list[dict[str, Any]], Any]:
        kwargs: dict[str, Any] = {"count": count, "continuation_token": token}
        if token is None:
            kwargs |= {"lang": self.settings.lang, "country": country}
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._max_attempts),
                retry=retry_if_exception(_transient),
                wait=wait_exponential_jitter(initial=2, max=60),
                sleep=self._sleep,
                reraise=True,
            ):
                with attempt:
                    return self._fetch_page(self.settings.app_id, **kwargs)
        except Exception as exc:  # the library raises assorted urllib/parsing errors
            raise SourceError(f"Play Store {country}: {exc.__class__.__name__}: {exc}") from exc
        raise AssertionError("unreachable")

    def _raw_item(self, review: dict[str, Any], countries: list[str]) -> RawItem:
        rid = review["reviewId"]
        payload = {
            "reviewId": rid,
            "content": review.get("content"),
            "score": review.get("score"),
            "thumbsUpCount": review.get("thumbsUpCount"),
            "reviewCreatedVersion": review.get("reviewCreatedVersion"),
            "appVersion": review.get("appVersion"),
            "at": iso(_utc(review.get("at"))),
            "replyContent": review.get("replyContent"),
            "repliedAt": iso(_utc(review.get("repliedAt"))),
            "author_hash": hash_author(review.get("userName"), self.salt),
            "_meta": {"countries": list(countries), "lang": self.settings.lang},
        }
        return RawItem(
            raw_id=f"play_store:{rid}",
            source_name=self.source_name,
            platform=self.platform,
            source_url=self.app_url(rid),
            run_id=self.run_id,
            payload=payload,
        )
