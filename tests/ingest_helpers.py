from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from discovery.config import HttpSettings
from discovery.ingest.http import PoliteHttp

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

HTTP_SETTINGS = HttpSettings(
    user_agent="discovery-test/0.1",
    timeout_seconds=5,
    max_attempts=3,
    min_interval_seconds=0,
    jitter_seconds=0,
)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_http(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    robots: str = "",
    exception: str | None = None,
    sleeps: list[float] | None = None,
) -> PoliteHttp:
    """PoliteHttp over a mock transport; robots.txt is served from `robots`."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return handler(request)

    record = sleeps if sleeps is not None else []
    return PoliteHttp(
        HTTP_SETTINGS,
        robots_exception=exception,
        transport=httpx.MockTransport(route),
        sleep=record.append,
    )
