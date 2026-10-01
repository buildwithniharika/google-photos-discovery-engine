"""Polite HTTP: robots.txt checks, per-host throttling with jitter, identifying User-Agent,
retries with backoff (honoring `Retry-After`), and timeouts.

`urllib.robotparser` ignores `*` / `$` wildcards and uses first-match order, so it misreads
Google- and Apple-style robots files. `RobotsRules` follows RFC 9309: the longest matching
rule wins, and `Allow` wins a tie.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from discovery.config import HttpSettings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RobotsDisallowed(Exception):
    def __init__(self, url: str):
        super().__init__(f"robots.txt disallows {url}")
        self.url = url


class HttpError(Exception):
    def __init__(self, url: str, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status} for {url}")
        self.url = url
        self.status = status
        self.retry_after = retry_after


# --- robots.txt ---------------------------------------------------------------


def _rule_regex(path: str) -> re.Pattern[str]:
    anchored = path.endswith("$")
    body = re.escape(path[:-1] if anchored else path).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


@dataclass
class RobotsRules:
    """Rules of the group that applies to our user agent."""

    rules: list[tuple[bool, str, re.Pattern[str]]] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, user_agent: str) -> RobotsRules:
        token = user_agent.split("/")[0].strip().lower()
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and (agents or rules):
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if value:
                    rules.append((key == "allow", value))
            else:
                last_was_agent = False
        if agents or rules:
            groups.append((agents, rules))

        specific = [r for a, r in groups if any(ag != "*" and ag in token for ag in a)]
        chosen = specific or [r for a, r in groups if "*" in a]
        merged = [rule for group in chosen for rule in group]
        return cls([(allow, path, _rule_regex(path)) for allow, path in merged])

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, path, rx in self.rules:
            if rx.match(target):
                key = (len(path), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


class RobotsPolicy:
    """Fetches and caches robots.txt per host."""

    def __init__(self, fetch: Callable[[str], httpx.Response], user_agent: str):
        self._fetch = fetch
        self._ua = user_agent
        self._cache: dict[str, RobotsRules | None] = {}
        self._lock = threading.Lock()

    def rules_for(self, url: str) -> RobotsRules | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if origin in self._cache:
                return self._cache[origin]
        try:
            resp = self._fetch(f"{origin}/robots.txt")
            if resp.status_code >= 500:
                rules: RobotsRules | None = None  # unreachable: treat as disallow-all (RFC 9309)
            elif resp.status_code >= 400:
                rules = RobotsRules()  # no robots.txt: everything allowed
            else:
                rules = RobotsRules.parse(resp.text, self._ua)
        except httpx.HTTPError as exc:
            log.warning(
                "Could not fetch robots.txt for %s (%s); treating as disallowed", origin, exc
            )
            rules = None
        with self._lock:
            self._cache[origin] = rules
        return rules

    def allowed(self, url: str) -> bool:
        rules = self.rules_for(url)
        return False if rules is None else rules.allowed(url)


# --- throttled client -----------------------------------------------------------


class Pacer:
    """At least `min_interval` (+ random jitter) between requests to the same host."""

    def __init__(
        self,
        min_interval: float,
        jitter: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self.jitter = jitter
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            gap = self.min_interval + random.uniform(0, self.jitter)
            last = self._last.get(host)
            now = self._clock()
            delay = 0.0 if last is None else max(0.0, last + gap - now)
            self._last[host] = now + delay
        if delay:
            self._sleep(delay)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class PoliteHttp:
    def __init__(
        self,
        settings: HttpSettings,
        *,
        robots_exception: str | None = None,
        min_interval: float | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.robots_exception = robots_exception
        self.exceptions_used: set[str] = set()
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "en"},
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self.pacer = Pacer(
            settings.min_interval_seconds if min_interval is None else min_interval,
            settings.jitter_seconds,
            clock=clock,
            sleep=sleep,
        )
        self.robots = RobotsPolicy(self._raw_get, settings.user_agent)

    def _raw_get(self, url: str) -> httpx.Response:
        self.pacer.wait(urlsplit(url).netloc)
        return self._client.get(url)

    def check_robots(self, url: str) -> None:
        """Raise `RobotsDisallowed` unless the URL is allowed or a recorded exception applies."""
        if self.robots.allowed(url):
            return
        if not self.robots_exception:
            raise RobotsDisallowed(url)
        origin = urlsplit(url).netloc
        if origin not in self.exceptions_used:
            log.warning("robots.txt disallows %s; proceeding under recorded exception", url)
            self.exceptions_used.add(origin)

    def get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        full = str(httpx.URL(url, params=params)) if params else url
        self.check_robots(full)
        host = urlsplit(full).netloc

        def wait(state) -> float:
            exc = state.outcome.exception()
            if isinstance(exc, HttpError) and exc.status == 429 and exc.retry_after is not None:
                return min(exc.retry_after, 120.0)
            return wait_exponential_jitter(initial=2, max=60)(state)

        def retryable(exc: BaseException) -> bool:
            if isinstance(exc, HttpError):
                return exc.status in RETRYABLE_STATUS
            return isinstance(exc, httpx.TransportError)

        for attempt in Retrying(
            stop=stop_after_attempt(self.settings.max_attempts),
            retry=retry_if_exception(retryable),
            wait=wait,
            sleep=self._sleep,
            reraise=True,
        ):
            with attempt:
                self.pacer.wait(host)
                resp = self._client.get(full)
                if resp.status_code >= 400:
                    raise HttpError(full, resp.status_code, _retry_after_seconds(resp))
                return resp
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttp:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def polite_pause(base: float, jitter: float = 1.0, sleep: Callable[[float], None] = time.sleep):
    sleep(base + random.uniform(0, jitter))
