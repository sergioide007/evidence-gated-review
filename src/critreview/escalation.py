"""Escalation gate. Deliberately NOT based on a cheap model's self-reported confidence.

Escalate to the strong model when:
  * the finding is verified and severity >= min_severity, OR
  * its area is in always_escalate_areas (and severity >= medium), OR
  * it falls in a reproducible random sample of everything else (so we can measure what the cheap path misses).
"""
from __future__ import annotations

import hashlib

from .config import Escalation
from .models import SEVERITIES, Finding


def _sampled(f: Finding, rate: float, seed: int) -> bool:
    h = hashlib.sha256(f"{seed}:{f.id}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32 < rate


def should_escalate(f: Finding, esc: Escalation) -> tuple[bool, str]:
    if not f.verified:
        return False, "unverified (dropped before escalation)"
    rank = SEVERITIES.index(f.severity)
    if rank >= SEVERITIES.index(esc.min_severity):
        return True, f"severity {f.severity} >= {esc.min_severity}"
    if f.area in esc.always_escalate_areas and rank >= SEVERITIES.index("medium"):
        return True, f"area {f.area} always escalates"
    if _sampled(f, esc.sample_rate_other, esc.seed):
        return True, "random audit sample"
    return False, "below threshold"
