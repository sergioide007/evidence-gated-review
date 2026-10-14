import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from critreview.cli import main as cli_main
from critreview.clients import FakeClient
from critreview.config import load_config
from critreview.doctor import looks_non_chat, run_doctor
from critreview.evals import score
from critreview.workflow import run_review

from helpers import ROOT, SUITE, cfg
from test_providers import clean_env


def all_lenses_garbage():
    return {k: "I could not do it, sorry." for k in ("[[LENS:reliability]]", "[[LENS:security]]",
                                                   "[[LENS:state_data]]", "[[LENS:ai_agents]]", "[[REPAIR:")}


class InvalidRunsAreNotMeasurements(unittest.TestCase):
    def run_bad(self):
        c = cfg()
        fc = FakeClient({}, default="not json at all")
        return run_review(SUITE / "project", c, {n: fc for n in c.tiers})

    def test_all_lenses_failed_is_flagged_invalid_with_the_reasons(self):
        res = score(self.run_bad(), SUITE / "expected.json")
        d = res.to_dict()
        self.assertFalse(res.valid)
        self.assertEqual(res.failed_lenses, 4)
        self.assertEqual(len(res.lens_errors), 4)
        self.assertIn("INVALID RUN", d["warning"])

    def test_precision_over_zero_findings_is_none_not_a_perfect_score(self):
        res = score(self.run_bad(), SUITE / "expected.json")
        self.assertIsNone(res.label_precision)
        self.assertIsNone(res.to_dict()["label_precision"])

    def test_the_valid_demo_run_is_unchanged(self):
        c = cfg()
        fc = FakeClient(json.loads((SUITE / "fake_responses.json").read_text(encoding="utf-8")))
        res = score(run_review(SUITE / "project", c, {n: fc for n in c.tiers}), SUITE / "expected.json")
        self.assertTrue(res.valid)
        self.assertEqual((res.recall, round(res.label_precision, 3)), (1.0, 0.833))
        self.assertNotIn("warning", res.to_dict())

    def test_cli_eval_exits_4_and_prints_the_lens_errors(self):
        with tempfile.TemporaryDirectory() as d:
            suite = Path(d) / "suite"
            shutil.copytree(SUITE, suite)
            (suite / "fake_responses.json").write_text(json.dumps({"[[LENS:": "not json"}), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli_main(["eval", "--suite", str(suite), "--config", str(ROOT / "config.example.toml")])
        self.assertEqual(code, 4)
        self.assertIn("INVALID RUN", err.getvalue())
        self.assertIn("reliability:", err.getvalue())
        self.assertFalse(json.loads(out.getvalue())["valid"])


class DoctorProbe(unittest.TestCase):
    def test_non_chat_heuristic(self):
        for bad in ("gemini-3.8-live", "gemini-2.5-flash-preview-tts", "text-embedding-004", "gpt-realtime",
                    "whisper-large-v3", "imagen-4"):
            self.assertTrue(looks_non_chat(bad), bad)
        for good in ("gemini-2.5-flash-lite", "openai/gpt-oss-120b", "deepseek-v4-flash", "llama-3.3-70b-versatile",
                     "antigravity-preview-09-2026"):
            self.assertFalse(looks_non_chat(good), good)

    def listed(self, c):
        return lambda p: [t.model for t in c.tiers.values()]

    def test_live_model_is_flagged_even_though_the_provider_lists_it(self):
        c = load_config(ROOT / "config.free.toml")
        c.tiers["mid"].model = "gemini-3.8-live"
        with clean_env(GEMINI_API_KEY="k", GROQ_API_KEY="k"):
            out = run_doctor(c, lister=lambda p: [t.model for t in c.tiers.values()])
        self.assertTrue(any((not x.ok) and "realtime/audio" in x.message for x in out))

    def test_probe_reports_ok_empty_and_failure_once_per_distinct_model(self):
        c = load_config(ROOT / "config.gemini-only.toml")     # three tiers, ONE distinct model
        calls = []

        def prober(p, m):
            calls.append((p, m))
            return "OK"

        with clean_env(GEMINI_API_KEY="k"):
            out = run_doctor(c, lister=self.listed(c), probe=True, prober=prober)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(x.ok for x in out if x.where.startswith("probe")))

        with clean_env(GEMINI_API_KEY="k"):
            empty = run_doctor(c, lister=self.listed(c), probe=True, prober=lambda p, m: "  ")
            boom = run_doctor(c, lister=self.listed(c), probe=True,
                              prober=lambda p, m: (_ for _ in ()).throw(RuntimeError("HTTP 400: model not supported")))
        self.assertTrue(any(x.where.startswith("probe") and not x.ok and "empty answer" in x.message for x in empty))
        self.assertTrue(any(x.where.startswith("probe") and not x.ok and "HTTP 400" in x.message for x in boom))

    def test_no_probe_means_no_calls(self):
        c = load_config(ROOT / "config.gemini-only.toml")
        with clean_env(GEMINI_API_KEY="k"):
            run_doctor(c, lister=self.listed(c), probe=False, prober=mock.Mock(side_effect=AssertionError))

    def test_gemini_only_profile_has_no_groq_limits_and_one_provider(self):
        c = load_config(ROOT / "config.gemini-only.toml")
        self.assertEqual({t.provider for t in c.tiers.values()}, {"gemini"})
        self.assertTrue(all(t.rpm is None and t.tpm is None for t in c.tiers.values()))


if __name__ == "__main__":
    unittest.main()


class RetryKnobsAndEvalOutputs(unittest.TestCase):
    def test_profiles_for_free_tiers_are_patient_with_503(self):
        for name in ("config.gemini-only.toml", "config.free.toml", "config.multi.toml"):
            c = load_config(ROOT / name)
            self.assertGreaterEqual(c.retry_attempts, 5, name)
            self.assertGreaterEqual(c.retry_base_delay_s, 2.0, name)
        d = load_config(ROOT / "config.example.toml")
        self.assertEqual((d.retry_attempts, d.retry_base_delay_s), (3, 0.5))   # defaults unchanged

    def test_config_rejects_absurd_retry_values(self):
        base = (ROOT / "config.gemini-only.toml").read_text(encoding="utf-8")
        for bad in (base.replace("retry_attempts = 5", "retry_attempts = 0"),
                    base.replace("retry_attempts = 5", "retry_attempts = 99")):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "c.toml"
                p.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(p)

    def test_workflow_uses_the_configured_attempts(self):
        from critreview.resilience import TransientError

        class Flaky:
            def __init__(self):
                self.n = 0

            def complete(self, system, prompt, max_out):
                self.n += 1
                raise TransientError("HTTP 503 high demand")

        c = cfg()
        c.retry_attempts, c.retry_base_delay_s = 4, 0.0
        flaky = Flaky()
        with mock.patch("time.sleep"):
            rep = run_review(SUITE / "project", c, {n: flaky for n in c.tiers})
        self.assertTrue(all(r.status != "ok" for r in rep.lens_results))
        self.assertGreaterEqual(flaky.n, 4 * 4)      # 4 lenses x 4 attempts (the breaker may cut some)

    def test_eval_saves_evidence_and_lists_unlabeled_findings(self):
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli_main(["eval", "--suite", str(SUITE), "--config", str(ROOT / "config.example.toml"),
                                 "--out", d])
            self.assertEqual(code, 0)
            for f in ("review.md", "risk_register.json", "eval_result.json", "audit.jsonl"):
                self.assertTrue((Path(d) / f).is_file(), f)
            saved = json.loads((Path(d) / "eval_result.json").read_text(encoding="utf-8"))
            self.assertFalse(saved["live"])
            self.assertIn("mid", saved["models"])
            self.assertEqual(len(saved["unlabeled"]), 1)
            self.assertIn("NO están en la verdad de referencia", err.getvalue())

    def test_lens_errors_are_single_line(self):
        c = cfg()
        fc = FakeClient({}, default="x")
        rep = run_review(SUITE / "project", c, {n: fc for n in c.tiers})
        for r in rep.lens_results:
            r.errors = ['TransientError: HTTP 503 [{\n  "error": {\n    "code": 503\n  }\n}\n]']
        res = score(rep, SUITE / "expected.json")
        self.assertTrue(all("\n" not in e for e in res.lens_errors))
