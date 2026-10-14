import contextlib
import email.message
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from critreview import clients
from critreview.audit import AuditLog
from critreview.budget import Budget
from critreview.cli import main as cli_main
from critreview.clients import (PROVIDERS, FakeClient, GuardedClient, OpenAICompatClient, ProviderError,
                                build_client, list_models)
from critreview.config import load_config
from critreview.doctor import run_doctor
from critreview.envfile import load_env_file
from critreview.ratelimit import RateLimiter, RateLimitImpossible, RateLimitWait
from critreview.resilience import CircuitBreaker, TransientError, retry_transient
from critreview.workflow import run_review

from helpers import ROOT, SUITE, cfg

KEYS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY")


def clean_env(**extra):
    base = {k: v for k, v in os.environ.items() if k not in KEYS}
    base.update(extra)
    return mock.patch.dict(os.environ, base, clear=True)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class RateLimiterTests(unittest.TestCase):
    def mk(self, **kw):
        c = Clock()
        return RateLimiter(clock=c.now, sleep=c.sleep, **kw), c

    def test_rpm_waits_for_the_window(self):
        rl, c = self.mk(rpm=2)
        rl.acquire(10), rl.acquire(10)
        self.assertEqual(c.slept, [])
        waited = rl.acquire(10)          # third call inside the same minute
        self.assertAlmostEqual(waited, 60.0, places=3)

    def test_tpm_waits_until_enough_tokens_expire(self):
        rl, c = self.mk(tpm=1000)
        rl.acquire(600)
        c.t = 10.0
        rl.acquire(300)                   # 900 <= 1000: fits
        self.assertEqual(c.slept, [])
        waited = rl.acquire(500)          # needs the first 600 to expire (at t=60)
        self.assertAlmostEqual(c.t, 60.0, places=3)
        self.assertGreater(waited, 0)

    def test_request_larger_than_the_whole_allowance_fails_fast_with_advice(self):
        rl, c = self.mk(tpm=1000)
        with self.assertRaises(RateLimitImpossible) as cm:
            rl.acquire(1001)
        self.assertIn("max_context_tokens", str(cm.exception))
        self.assertEqual(c.slept, [])

    def test_gives_up_instead_of_waiting_forever(self):
        rl, c = self.mk(rpm=1, max_wait_s=30)
        rl.acquire(1)
        with self.assertRaises(RateLimitWait):
            rl.acquire(1)

    def test_no_limits_never_waits(self):
        rl, c = self.mk()
        for _ in range(100):
            rl.acquire(10_000)
        self.assertEqual(c.slept, [])

    def test_guarded_client_uses_the_limiter_and_releases_budget_on_limit_error(self):
        class Lim:
            calls = []

            def acquire(self, n):
                self.calls.append(n)

        lim = Lim()
        b = Budget(max_calls=2)
        g = GuardedClient(FakeClient({}), "mid", b, CircuitBreaker(3, 1.0), lambda fn: fn(), limiter=lim)
        g.complete("s", "prompt", 100)
        self.assertEqual(len(lim.calls), 1)
        self.assertGreaterEqual(lim.calls[0], 100)

        class Boom:
            def acquire(self, n):
                raise RateLimitImpossible("too big")

        g2 = GuardedClient(FakeClient({}), "mid", Budget(max_calls=1), CircuitBreaker(3, 1.0), lambda fn: fn(),
                           limiter=Boom())
        with self.assertRaises(RateLimitImpossible):
            g2.complete("s", "p", 10)
        g2.budget.reserve(1)              # reservation was released


class RetryAfterTests(unittest.TestCase):
    def test_honors_the_providers_retry_after(self):
        slept, n = [], {"c": 0}

        def f():
            n["c"] += 1
            if n["c"] == 1:
                raise TransientError("429", retry_after=7.0)
            return "ok"

        self.assertEqual(retry_transient(f, sleep=slept.append, max_delay_s=30), "ok")
        self.assertEqual(slept, [7.0])

    def test_does_not_wait_when_retry_after_exceeds_our_cap(self):
        slept = []

        def f():
            raise TransientError("429", retry_after=500.0)

        with self.assertRaises(TransientError):
            retry_transient(f, sleep=slept.append, max_delay_s=30)
        self.assertEqual(slept, [])


def http_error(code, body=b"", headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://x", code, "err", h, io.BytesIO(body))


class HttpLayerTests(unittest.TestCase):
    def test_429_becomes_transient_with_retry_after(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(429, b"slow down", {"Retry-After": "12"})):
            with self.assertRaises(TransientError) as cm:
                clients._request_json("POST", "https://x", {}, {}, 5)
        self.assertEqual(cm.exception.retry_after, 12.0)

    def test_auth_error_is_not_retried_and_never_leaks_the_key(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401, b"bad key sk-SECRET123 given")):
            with self.assertRaises(ProviderError) as cm:
                clients._request_json("POST", "https://x", {}, {}, 5, secret="sk-SECRET123")
        self.assertIn("401", str(cm.exception))
        self.assertNotIn("sk-SECRET123", str(cm.exception))

    def test_5xx_is_transient(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(503)):
            with self.assertRaises(TransientError):
                clients._request_json("POST", "https://x", {}, {}, 5)


class CompatClientTests(unittest.TestCase):
    OK = {"choices": [{"message": {"content": "hola"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}

    def call(self, provider, env, resp=None):
        with clean_env(**env), mock.patch.object(clients, "_post_json", return_value=resp or self.OK) as m:
            comp = OpenAICompatClient(provider, "some-model").complete("sys", "prompt", 321)
        return comp, m.call_args

    def test_endpoints_auth_and_token_param_per_provider(self):
        cases = {
            "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY", "max_completion_tokens"),
            "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                       "GEMINI_API_KEY", "max_tokens"),
            "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "max_tokens"),
            "deepseek": ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY", "max_tokens"),
        }
        for prov, (url, env, param) in cases.items():
            comp, call = self.call(prov, {env: "k-123"})
            args, kwargs = call
            self.assertEqual(args[0], url, prov)
            self.assertEqual(args[1]["Authorization"], "Bearer k-123")
            self.assertEqual(args[2][param], 321)
            self.assertEqual(args[2]["model"], "some-model")
            self.assertEqual(kwargs.get("secret"), "k-123")      # so error bodies are scrubbed
            self.assertEqual((comp.text, comp.input_tokens, comp.output_tokens), ("hola", 11, 3))

    def test_google_api_key_is_accepted_for_gemini(self):
        comp, call = self.call("gemini", {"GOOGLE_API_KEY": "g-1"})
        self.assertEqual(call[0][1]["Authorization"], "Bearer g-1")

    def test_missing_key_names_the_variable_not_a_value(self):
        with clean_env():
            with self.assertRaises(ProviderError) as cm:
                OpenAICompatClient("groq", "m").complete("s", "p", 1)
        self.assertIn("GROQ_API_KEY", str(cm.exception))

    def test_missing_usage_falls_back_to_estimates_so_budgets_still_bind(self):
        comp, _ = self.call("groq", {"GROQ_API_KEY": "k"}, {"choices": [{"message": {"content": "x" * 400}}]})
        self.assertGreater(comp.input_tokens, 0)
        self.assertEqual(comp.output_tokens, 100)

    def test_null_content_and_bad_shape(self):
        comp, _ = self.call("groq", {"GROQ_API_KEY": "k"}, {"choices": [{"message": {"content": None}}]})
        self.assertEqual(comp.text, "")
        with self.assertRaises(ProviderError):
            self.call("groq", {"GROQ_API_KEY": "k"}, {"error": "weird"})

    def test_build_client_knows_every_provider(self):
        for p in PROVIDERS:
            self.assertIsInstance(build_client(p, "m"), OpenAICompatClient)
        with self.assertRaises(ValueError):
            build_client("nope", "m")

    def test_list_models_strips_the_gemini_prefix_and_sorts(self):
        data = {"data": [{"id": "models/gemini-b"}, {"id": "models/gemini-a"}, {"id": "gemini-a"}]}
        with clean_env(GEMINI_API_KEY="k"), mock.patch.object(clients, "_request_json", return_value=data):
            self.assertEqual(list_models("gemini"), ["gemini-a", "gemini-b"])


class EnvFileTests(unittest.TestCase):
    def test_loads_without_overriding_and_returns_names_only(self):
        with tempfile.TemporaryDirectory() as d, clean_env(GROQ_API_KEY="already"):
            f = Path(d) / ".env"
            f.write_text('# c\nGEMINI_API_KEY="abc"\nexport DEEPSEEK_API_KEY=xyz\nGROQ_API_KEY=other\nEMPTY=\n',
                         encoding="utf-8")
            names = load_env_file(f)
            self.assertEqual(sorted(names), ["DEEPSEEK_API_KEY", "GEMINI_API_KEY"])
            self.assertEqual(os.environ["GEMINI_API_KEY"], "abc")
            self.assertEqual(os.environ["GROQ_API_KEY"], "already")   # never overridden
            self.assertNotIn("EMPTY", os.environ)

    def test_missing_file_is_fine(self):
        self.assertEqual(load_env_file(Path("/nonexistent/.env")), [])

    def test_env_files_are_git_ignored_and_example_has_no_secrets(self):
        self.assertIn(".env", (ROOT / ".gitignore").read_text().splitlines())
        for line in (ROOT / ".env.example").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                self.assertEqual(line.split("=", 1)[1].strip(), "")


class ProfilesAndConfig(unittest.TestCase):
    def test_shipped_profiles_load_and_use_only_known_providers(self):
        for name in ("config.free.toml", "config.multi.toml", "config.example.toml"):
            c = load_config(ROOT / name)
            self.assertTrue(all(t.provider in (*PROVIDERS, "anthropic") for t in c.tiers.values()), name)

    def test_free_profile_needs_only_gemini_and_groq_and_throttles(self):
        c = load_config(ROOT / "config.free.toml")
        self.assertEqual({t.provider for t in c.tiers.values()}, {"gemini", "groq"})
        self.assertLessEqual(c.max_parallel, 2)
        self.assertTrue(all(t.tpm for n, t in c.tiers.items() if t.provider == "groq"))
        self.assertGreaterEqual(c.max_retry_wait_s, 60)

    def test_unknown_provider_and_bad_limits_rejected_at_load(self):
        base = (ROOT / "config.free.toml").read_text(encoding="utf-8")
        for bad in (base.replace('provider = "gemini"', 'provider = "geminii"'), base.replace("tpm = 8000", "tpm = 0", 1)):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "c.toml"
                p.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(p)


class PreflightAndParallelism(unittest.TestCase):
    def test_context_larger_than_tpm_fails_before_any_model_call(self):
        c = cfg()
        c.tiers["mid"].tpm = 500
        fc = FakeClient({})
        audit = AuditLog()
        with self.assertRaises(ValueError) as cm:
            run_review(SUITE / "project", c, {n: fc for n in c.tiers}, audit)
        self.assertIn("max_context_tokens", str(cm.exception))
        self.assertEqual(fc.calls, [])
        self.assertEqual(audit.records[-1]["event"], "aborted")

    def test_max_parallel_one_gives_the_same_result(self):
        import json
        resp = json.loads((SUITE / "fake_responses.json").read_text(encoding="utf-8"))
        outs = []
        for par in (0, 1):
            c = cfg()
            c.max_parallel = par
            outs.append(run_review(SUITE / "project", c, {n: FakeClient(resp) for n in c.tiers}))
        self.assertEqual((outs[0].verdict, outs[0].funnel()), (outs[1].verdict, outs[1].funnel()))


class DoctorTests(unittest.TestCase):
    def doc(self, lister, env, **kw):
        with clean_env(**env):
            return run_doctor(load_config(ROOT / "config.free.toml"), **kw, lister=lister)

    def test_missing_keys_are_reported_by_variable_name(self):
        out = self.doc(lambda p: [], {})
        bad = [c.message for c in out if not c.ok]
        self.assertTrue(any("GEMINI_API_KEY" in m for m in bad))
        self.assertTrue(any("GROQ_API_KEY" in m for m in bad))

    def test_unknown_model_id_is_flagged_with_a_pointer_to_the_models_command(self):
        out = self.doc(lambda p: ["some-other-model"], {"GEMINI_API_KEY": "k", "GROQ_API_KEY": "k"})
        self.assertTrue(any((not c.ok) and "critreview models --provider" in c.message for c in out))

    def test_all_good_and_privacy_note_for_gemini(self):
        def lister(p):
            return {"gemini": ["gemini-3.5-flash"], "groq": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]}[p]
        out = self.doc(lister, {"GEMINI_API_KEY": "k", "GROQ_API_KEY": "k"})
        self.assertTrue(all(c.ok for c in out), [c for c in out if not c.ok])
        self.assertTrue(any("improve" in c.message and "gemini" in c.where for c in out))

    def test_provider_error_is_reported_and_key_value_never_printed(self):
        def lister(p):
            raise ProviderError("HTTP 401: invalid")
        out = self.doc(lister, {"GEMINI_API_KEY": "SUPERSECRETVALUE", "GROQ_API_KEY": "SUPERSECRETVALUE"})
        text = " ".join(c.message + c.where for c in out)
        self.assertIn("401", text)
        self.assertNotIn("SUPERSECRETVALUE", text)

    def test_context_vs_tpm_check_with_target(self):
        c = load_config(ROOT / "config.free.toml")
        c.tiers["mid"].tpm = 300
        with clean_env(GEMINI_API_KEY="k", GROQ_API_KEY="k"):
            out = run_doctor(c, SUITE / "project", lister=lambda p: [t.model for t in c.tiers.values()])
        self.assertTrue(any(x.where == "context" and not x.ok for x in out))


class CliProviders(unittest.TestCase):
    def test_models_without_key_exits_1_naming_the_variable(self):
        err = io.StringIO()
        with clean_env(), contextlib.redirect_stderr(err):
            self.assertEqual(cli_main(["models", "--provider", "groq", "--env-file", "/nonexistent"]), 1)
        self.assertIn("GROQ_API_KEY", err.getvalue())

    def test_doctor_exit_code_reflects_failures(self):
        out = io.StringIO()
        with clean_env(), contextlib.redirect_stdout(out):
            code = cli_main(["doctor", "--config", str(ROOT / "config.free.toml"), "--env-file", "/nonexistent"])
        self.assertEqual(code, 1)
        self.assertIn("GEMINI_API_KEY", out.getvalue())


if __name__ == "__main__":
    unittest.main()
