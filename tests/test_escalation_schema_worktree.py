import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from critreview.config import Escalation
from critreview.escalation import should_escalate
from critreview.models import AREAS, FINDING_TYPES, LEVELS, SEVERITIES, Evidence, Finding, validate_finding_dict
from critreview.worktree import ephemeral_worktree

from helpers import ROOT


def mk(id_, area, sev, verified=True):
    f = Finding(id_, area, "t", [Evidence("a", 1, 1, "quote long enough")], "m", "t", "high", "d", "medium", "medium", "r", "c")
    f.severity, f.verified = sev, verified
    return f


class EscalationTests(unittest.TestCase):
    def test_unverified_never_escalates(self):
        self.assertFalse(should_escalate(mk("a", "security", "critical", verified=False), Escalation())[0])

    def test_high_severity_escalates(self):
        self.assertTrue(should_escalate(mk("a", "reliability", "high"), Escalation())[0])

    def test_always_escalate_area_medium(self):
        self.assertTrue(should_escalate(mk("a", "security", "medium"), Escalation())[0])

    def test_sampling_is_reproducible_and_roughly_calibrated(self):
        esc = Escalation(sample_rate_other=0.2, seed=1)
        got = [should_escalate(mk(f"id{i}", "cost", "low"), esc)[0] for i in range(2000)]
        again = [should_escalate(mk(f"id{i}", "cost", "low"), esc)[0] for i in range(2000)]
        self.assertEqual(got, again)
        self.assertTrue(0.15 < sum(got) / 2000 < 0.25)


class SchemaSyncTests(unittest.TestCase):
    def test_schema_enums_match_code(self):
        s = json.loads((ROOT / "schemas" / "finding.schema.json").read_text())["properties"]
        self.assertEqual(tuple(s["area"]["enum"]), AREAS)
        self.assertEqual(tuple(s["impact_level"]["enum"]), SEVERITIES)
        self.assertEqual(tuple(s["likelihood"]["enum"]), LEVELS)
        self.assertEqual(tuple(s["finding_type"]["enum"]), FINDING_TYPES)

    def test_skill_copy_of_schema_is_identical(self):
        a = (ROOT / "schemas" / "finding.schema.json").read_text()
        b = (ROOT / "skills" / "critical-technical-review" / "assets" / "finding.schema.json").read_text()
        self.assertEqual(a, b)

    def test_validator_flags_missing_fields(self):
        errs = validate_finding_dict({"id": "x", "area": "nope"})
        self.assertTrue(any("area" in e for e in errs))
        self.assertTrue(any("evidence" in e for e in errs))


def _make_repo(tmp: str) -> Path:
    repo = Path(tmp) / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (repo / "f.txt").write_text("hello world\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


class WorktreeTests(unittest.TestCase):
    def test_worktree_is_detached_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            with ephemeral_worktree(repo, "HEAD") as wt:
                self.assertEqual((wt / "f.txt").read_text(), "hello world\n")
                head = subprocess.run(["git", "-C", str(wt), "symbolic-ref", "-q", "HEAD"], capture_output=True)
                self.assertNotEqual(head.returncode, 0)  # detached HEAD: cannot advance a branch by accident
                kept = wt
            self.assertFalse(kept.exists())
            listing = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout
            self.assertEqual(len(listing.strip().splitlines()), 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses file permissions: read-only is best-effort, not a sandbox")
    def test_worktree_files_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            with ephemeral_worktree(repo, "HEAD") as wt:
                with self.assertRaises(PermissionError):
                    (wt / "f.txt").write_text("tamper")


if __name__ == "__main__":
    unittest.main()
