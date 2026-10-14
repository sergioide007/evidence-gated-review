import unittest
from pathlib import Path

from critreview.evidence import verify_evidence
from critreview.models import Evidence
from critreview.normalizer import Snapshot, redact

SNAP = Snapshot(root=Path("."), files={"A.java": ["class A {", "  void f() {", "    retry(post);  // blind retry", "  }", "}"]})


class EvidenceTests(unittest.TestCase):
    def test_exact_quote_verifies(self):
        self.assertTrue(verify_evidence(Evidence("A.java", 3, 3, "retry(post);  // blind retry"), SNAP).ok)

    def test_whitespace_is_normalized(self):
        self.assertTrue(verify_evidence(Evidence("A.java", 3, 3, "retry(post); // blind   retry"), SNAP).ok)

    def test_small_line_drift_is_tolerated(self):
        self.assertTrue(verify_evidence(Evidence("A.java", 1, 1, "retry(post);  // blind retry"), SNAP).ok)

    def test_hallucinated_quote_rejected(self):
        r = verify_evidence(Evidence("A.java", 3, 3, "circuitBreaker.execute(call)"), SNAP)
        self.assertFalse(r.ok)
        self.assertIn("not found", r.note)

    def test_missing_file_rejected(self):
        self.assertFalse(verify_evidence(Evidence("B.java", 1, 1, "class B { long enough }"), SNAP).ok)

    def test_trivial_quote_rejected(self):
        self.assertFalse(verify_evidence(Evidence("A.java", 4, 4, "}"), SNAP).ok)

    def test_real_quote_far_from_cited_lines_rejected(self):
        snap = Snapshot(root=Path("."), files={"A.java": ["x"] * 30 + ["dangerous(call);"]})
        r = verify_evidence(Evidence("A.java", 2, 2, "dangerous(call);"), snap)
        self.assertFalse(r.ok)
        self.assertIn("far from", r.note)

    def test_secret_redaction(self):
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", redact("key=AKIAABCDEFGHIJKLMNOP"))
        self.assertNotIn("hunter2hunter2", redact('password = "hunter2hunter2"'))


if __name__ == "__main__":
    unittest.main()
