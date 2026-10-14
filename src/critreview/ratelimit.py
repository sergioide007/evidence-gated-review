"""Client-side rate limiter (sliding 60 s window over requests and tokens).

Free API tiers are capped per minute (requests AND tokens), often per organization/project, so extra threads or keys
do not add capacity. Waiting here is cheaper and more predictable than burning retries on HTTP 429.
If a single request can never fit (estimated tokens > tokens-per-minute), we fail immediately with advice instead of
waiting forever.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimitImpossible(Exception):
    """One request is larger than the whole per-minute token allowance."""


class RateLimitWait(Exception):
    """Would have to wait longer than max_wait_s."""


class RateLimiter:
    WINDOW = 60.0

    def __init__(self, rpm: int | None = None, tpm: int | None = None, max_wait_s: float = 120.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.rpm, self.tpm, self.max_wait_s = rpm, tpm, max_wait_s
        self._clock, self._sleep = clock, sleep
        self._events: list[tuple[float, int]] = []  # (time, tokens)
        self._lock = threading.Lock()

    def _wait_needed(self, now: float, tokens: int) -> float:
        self._events = [(t, n) for t, n in self._events if now - t < self.WINDOW]
        need = 0.0
        if self.rpm is not None and len(self._events) + 1 > self.rpm:
            idx = len(self._events) + 1 - self.rpm - 1
            need = max(need, self._events[idx][0] + self.WINDOW - now)
        if self.tpm is not None:
            used = sum(n for _, n in self._events)
            if used + tokens > self.tpm:
                over, acc = used + tokens - self.tpm, 0
                for t, n in self._events:
                    acc += n
                    if acc >= over:
                        need = max(need, t + self.WINDOW - now)
                        break
        return need

    def acquire(self, est_tokens: int) -> float:
        """Block until the call fits the window. Returns seconds waited."""
        if self.tpm is not None and est_tokens > self.tpm:
            raise RateLimitImpossible(
                f"one request needs ~{est_tokens} tokens but the tier allows {self.tpm} per minute: "
                f"lower review.max_context_tokens or tiers.<name>.max_output_tokens, or use a larger-limit model")
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                need = self._wait_needed(now, est_tokens)
                if need <= 0:
                    self._events.append((now, est_tokens))
                    return waited
            if waited + need > self.max_wait_s:
                raise RateLimitWait(f"would wait {waited + need:.0f}s (> max_wait_s={self.max_wait_s:.0f}s)")
            self._sleep(need)
            waited += need
