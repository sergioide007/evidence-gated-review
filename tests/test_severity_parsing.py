import unittest

from critreview.models import Evidence, Finding
from critreview.parsing import extract_last_json, parse_lens_output
from critreview.severity import calibrate


def mk(impact, lik, det, ftype="fact"):
    return Finding("x", "reliability", "t", [Evidence("a", 1, 1, "quote long enough")], "m", "t", impact, "d",
                   lik, det, "r", "c", finding_type=ftype)


class SeverityTests(unittest.TestCase):
    def test_matrix(self):
        self.assertEqual(calibrate(mk("critical", "medium", "high")), "critical")
        self.assertEqual(calibrate(mk("medium", "low", "high")), "low")

    def test_low_detectability_bumps(self):
        self.assertEqual(calibrate(mk("medium", "medium", "low")), "high")

    def test_inference_cannot_be_critical(self):
        self.assertEqual(calibrate(mk("critical", "high", "low", ftype="inference")), "high")

    def test_model_proposed_severity_is_ignored(self):
        f = mk("low", "low", "high")
        f.proposed_severity = "critical"
        self.assertEqual(calibrate(f), "low")


class ParsingTests(unittest.TestCase):
    def test_takes_last_json_block(self):
        t = 'reasoning {"a": 1}\n```json\n{"findings": []}\n```'
        self.assertEqual(extract_last_json(t), {"findings": []})

    def test_braces_inside_strings(self):
        t = 'x {"findings": [{"id": "a}b"}]}'
        self.assertEqual(extract_last_json(t)["findings"][0]["id"], "a}b")

    def test_no_json_is_an_error_not_an_empty_result(self):
        raw, errs = parse_lens_output("No findings, everything looks fine.")
        self.assertEqual(raw, [])
        self.assertTrue(errs)

    def test_valid_empty_findings_is_not_an_error(self):
        raw, errs = parse_lens_output('{"findings": []}')
        self.assertEqual((raw, errs), ([], []))


if __name__ == "__main__":
    unittest.main()
