"""Deterministic severity calibration.

The model proposes impact/likelihood/detectability; severity is *computed*, so two runs with the same
inputs give the same ranking and a persuasive paragraph cannot talk its way into 'critical'.
"""
from __future__ import annotations

from .models import SEVERITIES, Finding

# (impact_level, likelihood) -> severity
_MATRIX = {
    ("critical", "high"): "critical", ("critical", "medium"): "critical", ("critical", "low"): "high",
    ("high", "high"): "critical", ("high", "medium"): "high", ("high", "low"): "medium",
    ("medium", "high"): "high", ("medium", "medium"): "medium", ("medium", "low"): "low",
    ("low", "high"): "medium", ("low", "medium"): "low", ("low", "low"): "low",
}


def bump(sev: str) -> str:
    i = SEVERITIES.index(sev)
    return SEVERITIES[min(i + 1, len(SEVERITIES) - 1)]


def calibrate(f: Finding) -> str:
    """Compute severity and leave a step-by-step trace on the finding (this IS the explanation: it is a rule)."""
    base = _MATRIX[(f.impact_level, f.likelihood)]
    sev = base
    trace = [f"matrix(impact={f.impact_level}, likelihood={f.likelihood}) -> {base}"]
    if f.detectability == "low":  # hard-to-detect failures are worse: nobody notices them in time
        bumped = bump(sev)
        trace.append(f"detectability=low: bump {sev} -> {bumped}" if bumped != sev else "detectability=low: already max")
        sev = bumped
    if f.finding_type == "inference" and sev == "critical":
        trace.append("finding_type=inference: cap critical -> high (needs a reproduced fact)")
        sev = "high"
    if f.proposed_severity != sev:
        trace.append(f"model proposed '{f.proposed_severity}'; ignored, rule result '{sev}' stands")
    f.severity, f.severity_trace = sev, trace
    return sev
