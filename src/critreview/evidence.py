"""Deterministic evidence verifier. This is the core guardrail: it is NOT a language model.

A finding survives only if every cited quote really exists in the cited file, near the cited lines.
It cannot prove a finding is *right*; it proves the finding is *anchored in the code*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Evidence, Finding
from .normalizer import Snapshot, neutralize

LINE_TOLERANCE = 3   # models miscount lines; we accept small drift and report the relocation
MIN_QUOTE_CHARS = 8  # trivial quotes ('}' , 'return') prove nothing

_WS = re.compile(r"\s+")
# Models often copy the "   12| " gutter we print before each line. That is presentation, not code.
_GUTTER = re.compile(r"^[ \t]*\d{1,6}\|[ ]?", re.MULTILINE)


def _norm(s: str) -> str:
    return _WS.sub(" ", neutralize(s)).strip()


def _norm_quote(s: str) -> str:
    return _norm(_GUTTER.sub("", s))


def _norm_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


@dataclass
class EvidenceResult:
    ok: bool
    note: str


def verify_evidence(ev: Evidence, snap: Snapshot) -> EvidenceResult:
    lines = snap.files.get(_norm_path(ev.file))
    if lines is None:
        return EvidenceResult(False, f"{ev.file}: file not in snapshot")
    quote = _norm_quote(ev.quote)
    if len(quote) < MIN_QUOTE_CHARS:
        return EvidenceResult(False, f"{ev.file}: quote too short to be meaningful")
    if ev.start_line > ev.end_line or ev.start_line > len(lines):
        return EvidenceResult(False, f"{ev.file}: invalid line range {ev.start_line}-{ev.end_line}")
    lo = max(1, ev.start_line - LINE_TOLERANCE)
    hi = min(len(lines), ev.end_line + LINE_TOLERANCE)
    window = _norm("\n".join(lines[lo - 1:hi]))
    if quote in window:
        return EvidenceResult(True, f"{ev.file}:{ev.start_line}-{ev.end_line} verified")
    whole = _norm("\n".join(lines))
    if quote in whole:
        return EvidenceResult(False, f"{ev.file}: quote exists but far from cited lines {ev.start_line}-{ev.end_line}")
    return EvidenceResult(False, f"{ev.file}: quote not found (possible hallucination)")


def verify_finding(f: Finding, snap: Snapshot) -> bool:
    notes: list[str] = []
    ok = True
    for ev in f.evidence:
        r = verify_evidence(ev, snap)
        notes.append(r.note)
        ok = ok and r.ok
    f.verified = ok
    f.verification_notes = notes
    return ok
