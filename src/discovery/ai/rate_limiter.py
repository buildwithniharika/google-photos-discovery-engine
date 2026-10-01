"""Client-side rate limiting for Groq: request and token buckets plus a concurrency cap."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_EPSILON = 1e-6  # float rounding can leave a ~1e-17 deficit that would never refill
_MIN_SLEEP = 0.001


class TokenBucket:
    """Classic token bucket. `level` may go negative after `adjust`, which creates debt."""

    def __init__(self, capacity: float, per_second: float, clock: Callable[[], float]):
        self.capacity = capacity
        self.per_second = per_second
        self.level = capacity
        self._clock = clock
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self.level = min(self.capacity, self.level + (now - self._last) * self.per_second)
        self._last = now

    def wait_time(self, amount: float) -> float:
        self._refill()
        deficit = min(amount, self.capacity) - self.level
        return 0.0 if deficit <= _EPSILON else deficit / self.per_second

    def take(self, amount: float) -> None:
        self._refill()
        self.level -= amount

    def adjust(self, delta: float) -> None:
        self._refill()
        self.level -= delta


class RateLimiter:
    """Paces calls to one model so they stay under its requests/tokens per minute.

    Requests are paced smoothly (bucket capacity 1) so no 60-second window exceeds the
    configured RPM. A 429 `retry-after` or an exhausted token header pauses every thread.
    """

    def __init__(
        self,
        requests_per_minute: float,
        tokens_per_minute: float | None,
        max_concurrency: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._requests = TokenBucket(1, requests_per_minute / 60.0, clock)
        self._tokens = (
            TokenBucket(tokens_per_minute, tokens_per_minute / 60.0, clock)
            if tokens_per_minute
            else None
        )
        self._paused_until = 0.0

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + max(0.0, seconds))

    def adjust_tokens(self, delta: float) -> None:
        """Correct the token bucket once actual usage is known (delta = actual - estimate)."""
        if self._tokens is not None and delta:
            with self._lock:
                self._tokens.adjust(delta)

    def _acquire(self, est_tokens: float) -> None:
        while True:
            with self._lock:
                wait = max(
                    self._paused_until - self._clock(),
                    self._requests.wait_time(1),
                    self._tokens.wait_time(est_tokens) if self._tokens else 0.0,
                )
                if wait <= 0:
                    self._requests.take(1)
                    if self._tokens:
                        self._tokens.take(est_tokens)
                    return
            self._sleep(max(wait, _MIN_SLEEP))

    @contextmanager
    def slot(self, est_tokens: float = 0) -> Iterator[None]:
        with self._slots:
            self._acquire(est_tokens)
            yield
