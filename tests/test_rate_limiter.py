from __future__ import annotations

from discovery.ai.rate_limiter import RateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def max_in_window(times: list[float], window: float = 60.0) -> int:
    eps = 1e-6  # float noise at the window edge
    return max(sum(1 for t in times if start <= t < start + window - eps) for start in times)


def test_requests_never_exceed_rpm_in_any_minute():
    t = FakeTime()
    limiter = RateLimiter(28, None, max_concurrency=4, clock=t.clock, sleep=t.sleep)
    times = []
    for _ in range(100):
        with limiter.slot():
            times.append(t.now)
    assert max_in_window(times) <= 28


def test_token_bucket_delays_when_tokens_run_out():
    t = FakeTime()
    limiter = RateLimiter(1000, 6000, max_concurrency=1, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        with limiter.slot(est_tokens=2000):
            pass
    assert t.now < 1  # 6000 tokens available up front
    with limiter.slot(est_tokens=2000):
        pass
    assert t.now >= 19  # needs 2000 tokens at 100 tokens/s


def test_adjust_tokens_creates_debt():
    t = FakeTime()
    limiter = RateLimiter(1000, 6000, max_concurrency=1, clock=t.clock, sleep=t.sleep)
    with limiter.slot(est_tokens=1000):
        pass
    limiter.adjust_tokens(5000)  # the call actually used 6000 tokens
    with limiter.slot(est_tokens=600):
        pass
    assert t.now >= 5.5


def test_pause_blocks_next_call():
    t = FakeTime()
    limiter = RateLimiter(1000, None, max_concurrency=1, clock=t.clock, sleep=t.sleep)
    limiter.pause(12.5)
    with limiter.slot():
        pass
    assert t.now >= 12.5
