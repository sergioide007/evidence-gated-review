import unittest

from critreview.budget import Budget, BudgetExceeded
from critreview.resilience import CircuitBreaker, CircuitOpenError, TransientError, retry_transient


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class BreakerTests(unittest.TestCase):
    def test_opens_after_threshold_then_half_opens(self):
        clk = Clock()
        cb = CircuitBreaker(2, 10.0, clock=clk)

        def boom():
            raise RuntimeError("x")

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cb.call(boom)
        self.assertEqual(cb.state, "open")
        with self.assertRaises(CircuitOpenError):
            cb.call(lambda: 1)
        clk.t = 11.0
        self.assertEqual(cb.state, "half_open")
        self.assertEqual(cb.call(lambda: 42), 42)
        self.assertEqual(cb.state, "closed")

    def test_half_open_failure_reopens(self):
        clk = Clock()
        cb = CircuitBreaker(1, 5.0, clock=clk)
        with self.assertRaises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
        clk.t = 6.0
        with self.assertRaises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("y")))
        self.assertEqual(cb.state, "open")


class RetryTests(unittest.TestCase):
    def test_retries_only_transient_and_is_bounded(self):
        n = {"c": 0}

        def flaky():
            n["c"] += 1
            raise TransientError("503")

        with self.assertRaises(TransientError):
            retry_transient(flaky, attempts=3, sleep=lambda s: None)
        self.assertEqual(n["c"], 3)

    def test_non_transient_not_retried(self):
        n = {"c": 0}

        def bad():
            n["c"] += 1
            raise ValueError("400")

        with self.assertRaises(ValueError):
            retry_transient(bad, attempts=5, sleep=lambda s: None)
        self.assertEqual(n["c"], 1)

    def test_succeeds_after_transient(self):
        seq = iter([TransientError("a"), "ok"])

        def f():
            v = next(seq)
            if isinstance(v, Exception):
                raise v
            return v

        self.assertEqual(retry_transient(f, sleep=lambda s: None), "ok")


class BudgetTests(unittest.TestCase):
    def test_call_limit(self):
        b = Budget(max_calls=1)
        b.precheck(10)
        b.charge("mid", 10, 5)
        with self.assertRaises(BudgetExceeded):
            b.precheck(10)

    def test_input_token_limit(self):
        with self.assertRaises(BudgetExceeded):
            Budget(max_input_tokens=100).precheck(101)

    def test_usd_accounting_only_with_prices(self):
        b = Budget()
        b.charge("mid", 1_000_000, 0)  # no price configured => 0 usd, never invented
        self.assertEqual(b.used.usd, 0.0)
        b.charge("mid", 1_000_000, 0, price_in_per_mtok=2.0)
        self.assertAlmostEqual(b.used.usd, 2.0)


if __name__ == "__main__":
    unittest.main()
