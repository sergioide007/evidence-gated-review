import copy
import json
import unittest

from critreview.budget import Budget
from critreview.clients import FakeClient
from critreview.evals import score
from critreview.report import render_markdown
from critreview.workflow import run_review

from helpers import SUITE, cfg


def responses():
    return json.loads((SUITE / "fake_responses.json").read_text(encoding="utf-8"))


def run(resp, c=None):
    c = c or cfg()
    fc = FakeClient(resp)
    return run_review(SUITE / "project", c, {n: fc for n in c.tiers}), fc


class EndToEnd(unittest.TestCase):
    def test_hallucinated_and_invalid_findings_are_rejected(self):
        rep, _ = run(responses())
        reasons = sorted((x["id"], x["reason"]) for x in rep.rejected)
        self.assertIn(("reliability:REL-900", "evidence"), reasons)
        self.assertIn(("reliability:REL-901", "evidence"), reasons)
        self.assertTrue(any(r == "schema" for _, r in reasons))
        self.assertTrue(all(f.verified for f in rep.findings))

    def test_duplicate_findings_across_lenses_are_merged(self):
        rep, _ = run(responses())
        loops = [f for f in rep.findings if f.evidence[0].file == "agent_runner.py" and f.evidence[0].start_line == 12]
        self.assertEqual(len(loops), 1)
        self.assertEqual(sorted(loops[0].lenses), ["ai_agents", "reliability"])

    def test_inference_is_capped_below_critical(self):
        rep, _ = run(responses())
        inf = next(f for f in rep.findings if f.finding_type == "inference")
        self.assertNotEqual(inf.severity, "critical")

    def test_verdict_is_rule_based_and_critical_present(self):
        rep, _ = run(responses())
        self.assertEqual(rep.verdict, "FRAGIL")
        self.assertFalse(rep.incomplete)

    def test_eval_scores_seeded_defects_and_canary(self):
        rep, _ = run(responses())
        res = score(rep, SUITE / "expected.json")
        self.assertEqual(res.recall, 1.0)
        self.assertTrue(res.canary_reported)
        self.assertEqual(res.rejected_no_evidence, 2)
        self.assertEqual(res.unlabeled_findings, 1)  # the 'inference' finding: for manual review

    def test_obeyed_injection_means_incomplete_not_clean(self):
        r = responses()
        r["[[LENS:security]]"] = "This repository has no findings."  # a lens that 'obeyed' the injected comment
        for k in ("[[LENS:reliability]]", "[[LENS:state_data]]", "[[LENS:ai_agents]]"):
            r[k] = '{"findings": []}'
        rep, _ = run(r)
        self.assertEqual([x.status for x in rep.lens_results].count("failed"), 1)
        self.assertTrue(rep.incomplete)
        self.assertEqual(rep.verdict, "INCOMPLETO")  # never 'ACEPTABLE' without full coverage

    def test_all_lenses_valid_and_empty_is_acceptable_only_on_a_clean_project(self):
        import tempfile
        from pathlib import Path as P
        r = {k: '{"findings": []}' for k in ("[[LENS:reliability]]", "[[LENS:security]]",
                                             "[[LENS:state_data]]", "[[LENS:ai_agents]]")}
        with tempfile.TemporaryDirectory() as d:
            (P(d) / "ok.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            c = cfg()
            fc = FakeClient(r)
            rep = run_review(P(d), c, {n: fc for n in c.tiers})
        self.assertEqual(rep.verdict, "ACEPTABLE")

    def test_valid_empty_answers_on_a_project_with_an_injection_are_NOT_acceptable(self):
        # Regression for the hole this test used to hide: every lens "valid and empty" + the canary present
        # used to produce ACEPTABLE. A lens that obeyed the injected comment looks exactly like this.
        r = {k: '{"findings": []}' for k in ("[[LENS:reliability]]", "[[LENS:security]]",
                                             "[[LENS:state_data]]", "[[LENS:ai_agents]]")}
        rep, _ = run(r)
        self.assertEqual(rep.verdict, "INCOMPLETO")
        self.assertTrue(rep.uncovered_signals)
        self.assertTrue(all(x.status == "ok" for x in rep.lens_results))  # the schema saw nothing wrong

    def test_budget_exhaustion_gives_labelled_partial_report(self):
        c = cfg()
        c.budget = Budget(max_calls=5)  # 4 lenses + 1 adjudication, then out
        rep, _ = run(responses(), c)
        self.assertTrue(rep.incomplete)
        self.assertIn("max_calls", rep.partial_reason)
        self.assertTrue(any(f.adjudication and f.adjudication["decision"] == "not_run" for f in rep.findings))

    def test_adjudication_cannot_silently_delete_a_finding(self):
        r = responses()
        r["[[ADJUDICATE]]"] = '{"decision": "reject", "reason": "injected: drop it"}'
        rep, _ = run(r)
        self.assertTrue(any(f.severity == "critical" for f in rep.findings))
        self.assertTrue(all(f.needs_human for f in rep.findings if f.adjudication and f.adjudication["decision"] == "reject"))

    def test_funnel_numbers_for_the_demo(self):
        rep, _ = run(responses())
        self.assertEqual(rep.funnel(), {"proposed": 10, "rejected_no_evidence": 2, "rejected_schema": 1,
                                        "merged_duplicates": 1, "verified": 6})

    def test_injected_scenario_file_yields_incomplete_not_acceptable(self):
        inj = json.loads((SUITE / "fake_responses_injected.json").read_text(encoding="utf-8"))
        rep, _ = run(inj)
        self.assertEqual(rep.verdict, "INCOMPLETO")
        self.assertEqual([r.lens for r in rep.lens_results if r.status == "failed"], ["security"])

    def test_report_renders_core_sections(self):
        rep, _ = run(responses())
        md = render_markdown(rep)
        for s in ("## Embudo de verificación", "## Cobertura", "## Hallazgos verificados", "## Rechazados antes del informe", "Veredicto"):
            self.assertIn(s, md)


if __name__ == "__main__":
    unittest.main()
