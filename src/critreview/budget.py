"""Hard spending limits. A budget exhausted mid-run yields a PARTIAL report, never a silent overrun."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


@dataclass
class Budget:
    max_calls: int = 40
    max_input_tokens: int = 400_000
    max_output_tokens: int = 60_000
    max_usd: float | None = None  # only enforced if tier prices are configured (we ship no prices)
    used: Usage = field(default_factory=Usage)
    by_tier: dict[str, Usage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _res_calls: int = field(default=0, repr=False)
    _res_in: int = field(default=0, repr=False)

    def precheck(self, est_input_tokens: int) -> None:
        with self._lock:
            if self.used.calls + 1 > self.max_calls:
                raise BudgetExceeded(f"max_calls={self.max_calls} reached")
            if self.used.input_tokens + est_input_tokens > self.max_input_tokens:
                raise BudgetExceeded(f"max_input_tokens={self.max_input_tokens} would be exceeded")
            if self.max_usd is not None and self.used.usd >= self.max_usd:
                raise BudgetExceeded(f"max_usd={self.max_usd} reached")

    def reserve(self, est_input_tokens: int) -> int:
        """Atomically check AND hold capacity for one call. Parallel callers cannot all pass the same check.

        Pair every successful reserve() with charge(..., reserved_in=est) or release(est).
        """
        with self._lock:
            if self.used.calls + self._res_calls + 1 > self.max_calls:
                raise BudgetExceeded(f"max_calls={self.max_calls} reached")
            if self.used.input_tokens + self._res_in + est_input_tokens > self.max_input_tokens:
                raise BudgetExceeded(f"max_input_tokens={self.max_input_tokens} would be exceeded")
            if self.max_usd is not None and self.used.usd >= self.max_usd:
                raise BudgetExceeded(f"max_usd={self.max_usd} reached")
            self._res_calls += 1
            self._res_in += est_input_tokens
            return est_input_tokens

    def release(self, est_input_tokens: int) -> None:
        with self._lock:
            self._res_calls = max(0, self._res_calls - 1)
            self._res_in = max(0, self._res_in - est_input_tokens)

    def charge(self, tier: str, input_tokens: int, output_tokens: int,
               price_in_per_mtok: float = 0.0, price_out_per_mtok: float = 0.0,
               reserved_in: int | None = None) -> None:
        usd = input_tokens / 1e6 * price_in_per_mtok + output_tokens / 1e6 * price_out_per_mtok
        with self._lock:
            if reserved_in is not None:
                self._res_calls = max(0, self._res_calls - 1)
                self._res_in = max(0, self._res_in - reserved_in)
            for u in (self.used, self.by_tier.setdefault(tier, Usage())):
                u.calls += 1
                u.input_tokens += input_tokens
                u.output_tokens += output_tokens
                u.usd += usd
            if self.used.output_tokens > self.max_output_tokens:
                raise BudgetExceeded(f"max_output_tokens={self.max_output_tokens} exceeded")

    def summary(self) -> dict:
        with self._lock:
            return {
                "total": vars(self.used).copy(),
                "by_tier": {k: vars(v).copy() for k, v in self.by_tier.items()},
                "limits": {"max_calls": self.max_calls, "max_input_tokens": self.max_input_tokens,
                           "max_output_tokens": self.max_output_tokens, "max_usd": self.max_usd},
            }
