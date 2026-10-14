#!/usr/bin/env python3
"""Verify that findings are anchored in the code.

Usage: python verify_evidence.py findings.json --root /path/to/repo
Input: {"findings": [ ...finding objects... ]}   Exit code 1 if any finding is rejected.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))  # works from a repo checkout

from critreview.evidence import verify_finding  # noqa: E402
from critreview.models import finding_from_dict, validate_finding_dict  # noqa: E402
from critreview.normalizer import build_snapshot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("findings")
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    snap = build_snapshot(Path(a.root))
    data = json.loads(Path(a.findings).read_text(encoding="utf-8"))
    bad = 0
    for d in data.get("findings", []):
        errs = validate_finding_dict(d)
        if errs:
            print(f"REJECT {d.get('id', '?')}: schema: {'; '.join(errs)}")
            bad += 1
            continue
        f = finding_from_dict(d, "manual")
        ok = verify_finding(f, snap)
        print(("OK     " if ok else "REJECT ") + f"{f.id}: {'; '.join(f.verification_notes)}")
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
