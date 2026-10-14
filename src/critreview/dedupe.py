"""Merge duplicate findings reported by different lenses (same area, overlapping evidence)."""
from __future__ import annotations

from .models import SEVERITIES, Finding


def _overlap(a: Finding, b: Finding) -> bool:
    for ea in a.evidence:
        for eb in b.evidence:
            if ea.file == eb.file and ea.start_line <= eb.end_line and eb.start_line <= ea.end_line:
                return True
    return False


def dedupe(findings: list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: -SEVERITIES.index(x.severity)):
        twin = next((k for k in kept if k.area == f.area and _overlap(k, f)), None)
        if twin is None:
            kept.append(f)
        else:
            twin.lenses = sorted(set(twin.lenses) | set(f.lenses))
            twin.verification_notes.append(f"merged duplicate '{f.id}' from {f.lenses}")
    return kept
