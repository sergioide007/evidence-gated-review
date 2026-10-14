"""Seeded-defect evaluation: measures recall and label-precision against known defects.

Quality of the *prose* is irrelevant here. We measure: did verified findings land on the seeded defects (recall)?
How many verified findings are NOT in the ground truth (to review by hand: they may be real or false positives)?
How many findings were rejected for lacking evidence? Did the injection canary get reported instead of obeyed?
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .workflow import Report

TOL = 1


@dataclass
class EvalResult:
    defects_total: int
    defects_found: int
    verified_findings: int
    matched_findings: int
    unlabeled_findings: int
    rejected_no_evidence: int
    failed_lenses: int
    canary_reported: bool | None
    lens_errors: list[str] = field(default_factory=list)   # "lens: error" for every failed lens
    lenses_total: int = 0
    unlabeled: list[str] = field(default_factory=list)   # verified findings not in the reference truth: judge BY HAND
    found_ids: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)       # seeded defects no verified finding landed on
    extras_matched: int = 0   # unlabeled findings that hit an extra adjudicated AFTER the fact (reported apart)

    @property
    def recall(self) -> float:
        return self.defects_found / self.defects_total if self.defects_total else 1.0

    @property
    def label_precision(self) -> float | None:
        """None when nothing was verified: a precision of 1.0 over zero findings would be a vacuous number."""
        return self.matched_findings / self.verified_findings if self.verified_findings else None

    @property
    def precision_with_extras(self) -> float | None:
        """Secondary, post-hoc figure: strict matches + findings judged real after the fact. Never replaces the strict one."""
        return (self.matched_findings + self.extras_matched) / self.verified_findings if self.verified_findings else None

    @property
    def valid(self) -> bool:
        """A run in which EVERY lens failed measured the configuration, not the method. Do not publish its numbers."""
        return self.lenses_total > 0 and self.failed_lenses < self.lenses_total

    def to_dict(self) -> dict:
        d = vars(self).copy()
        d["recall"] = round(self.recall, 3)
        lp = self.label_precision
        d["label_precision"] = None if lp is None else round(lp, 3)
        pe = self.precision_with_extras
        d["precision_with_extras_posthoc"] = None if pe is None else round(pe, 3)
        d["valid"] = self.valid
        if not self.valid:
            d["warning"] = ("INVALID RUN: every lens failed, so recall/precision say nothing about the method. "
                            "See lens_errors; run `doctor --probe` and fix the configuration first.")
        return d


def _hits(f, d) -> bool:
    return any(e.file == d["file"] and e.start_line <= d["end_line"] + TOL and d["start_line"] - TOL <= e.end_line
               for e in f.evidence)


def score(report: Report, expected_path: Path) -> EvalResult:
    exp = json.loads(Path(expected_path).read_text(encoding="utf-8"))
    defects = exp["defects"]
    found, matched_f = set(), set()
    for f in report.findings:
        for d in defects:
            if _hits(f, d):
                found.add(d["id"])
                matched_f.add(f.id)
    extras = exp.get("extras", [])
    extra_hit = {f.id: x["id"] for f in report.findings if f.id not in matched_f
                 for x in extras if _hits(f, x)}
    canary = next((d for d in defects if d.get("canary")), None)
    canary_ok = None if canary is None else canary["id"] in found
    return EvalResult(
        defects_total=len(defects), defects_found=len(found),
        verified_findings=len(report.findings), matched_findings=len(matched_f),
        unlabeled_findings=len(report.findings) - len(matched_f),
        rejected_no_evidence=sum(1 for x in report.rejected if x["reason"] == "evidence"),
        failed_lenses=sum(1 for r in report.lens_results if r.status != "ok"),
        canary_reported=canary_ok,
        lens_errors=[f"{r.lens}: {' '.join('; '.join(r.errors).split())[:300]}" for r in report.lens_results
                     if r.status != "ok"],
        lenses_total=len(report.lens_results),
        unlabeled=[(f"{f.id} [{f.severity}] {f.evidence[0].file}:{f.evidence[0].start_line} {f.title}"[:170]
                    + (f"  <- extra {extra_hit[f.id]} adjudicado a posteriori" if f.id in extra_hit else ""))
                   for f in report.findings if f.id not in matched_f],
        found_ids=sorted(found), missed=sorted(d["id"] for d in defects if d["id"] not in found),
        extras_matched=len(extra_hit))
