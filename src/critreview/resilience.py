"""Circuit breaker and bounded retries for model calls (stdlib only, injectable clock for tests)."""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """Retryable failure (timeout, 429, 5xx). Anything else is NOT retried.

    `retry_after` (seconds) carries the provider's own Retry-After hint when it sent one.
    """

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 3, reset_timeout_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state = self.CLOSED
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN and self._clock() - self._opened_at >= self.reset_timeout_s:
                self._state = self.HALF_OPEN
            return self._state

    def call(self, fn: Callable[[], T]) -> T:
        if self.state == self.OPEN:
            raise CircuitOpenError("circuit open: failing fast instead of piling up latency")
        try:
            result = fn()
        except Exception:
            with self._lock:
                self._failures += 1
                if self._state == self.HALF_OPEN or self._failures >= self.failure_threshold:
                    self._state = self.OPEN
                    self._opened_at = self._clock()
            raise
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED
        return result


def retry_transient(fn: Callable[[], T], *, attempts: int = 3, base_delay_s: float = 0.5,
                    max_delay_s: float = 8.0, sleep: Callable[[float], None] = time.sleep,
                    rng: random.Random | None = None) -> T:
    """Bounded exponential backoff with full jitter. Retries ONLY TransientError."""
    rng = rng or random.Random()
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as e:
            last = e
            if i == attempts - 1:
                break
            hint = e.retry_after
            if hint is not None and hint > max_delay_s:
                break  # the provider asks for longer than we are willing to wait: retrying now is pointless
            sleep(hint if hint is not None else rng.uniform(0, min(max_delay_s, base_delay_s * (2 ** i))))
    assert last is not None
    raise last
