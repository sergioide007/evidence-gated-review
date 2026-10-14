"""Data model. Enumerations here are mirrored in schemas/finding.schema.json (a test keeps them in sync)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ("low", "medium", "high", "critical")
LEVELS = ("low", "medium", "high")
AREAS = (
    "architecture", "reliability", "security", "state_data", "concurrency",
    "scalability", "observability", "cost", "ai_agents",
)
FINDING_TYPES = ("fact", "inference")


@dataclass(frozen=True)
class Evidence:
    file: str
    start_line: int
    end_line: int
    quote: str


@dataclass
class Finding:
    id: str
    area: str
    title: str
    evidence: list[Evidence]
    failure_mechanism: str
    trigger: str
    impact_level: str
    impact_description: str
    likelihood: str
    detectability: str
    recommendation: str
    tradeoff: str
    finding_type: str = "fact"
    proposed_severity: str = "medium"
    severity: str = "medium"  # computed by severity.calibrate, never taken from the model
    severity_trace: list[str] = field(default_factory=list)  # how the rule got there (deterministic, auditable)
    lenses: list[str] = field(default_factory=list)
    verified: bool = False
    verification_notes: list[str] = field(default_factory=list)
    adjudication: dict[str, Any] | None = None
    needs_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def validate_finding_dict(d: Any) -> list[str]:
    """Return a list of validation errors (empty list == valid). Deliberately dependency-free."""
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["finding must be an object"]
    for key in ("id", "title", "failure_mechanism", "trigger", "impact_description",
                "recommendation", "tradeoff"):
        if not _is_nonempty_str(d.get(key)):
            errs.append(f"{key}: required non-empty string")
    if d.get("area") not in AREAS:
        errs.append(f"area: must be one of {AREAS}")
    if d.get("impact_level") not in SEVERITIES:
        errs.append(f"impact_level: must be one of {SEVERITIES}")
    for key in ("likelihood", "detectability"):
        if d.get(key) not in LEVELS:
            errs.append(f"{key}: must be one of {LEVELS}")
    if d.get("finding_type", "fact") not in FINDING_TYPES:
        errs.append(f"finding_type: must be one of {FINDING_TYPES}")
    if d.get("proposed_severity", "medium") not in SEVERITIES:
        errs.append(f"proposed_severity: must be one of {SEVERITIES}")
    ev = d.get("evidence")
    if not isinstance(ev, list) or not ev:
        errs.append("evidence: at least one item required")
    else:
        for i, e in enumerate(ev):
            if not isinstance(e, dict):
                errs.append(f"evidence[{i}]: must be an object")
                continue
            if not _is_nonempty_str(e.get("file")):
                errs.append(f"evidence[{i}].file: required")
            for k in ("start_line", "end_line"):
                if not isinstance(e.get(k), int) or isinstance(e.get(k), bool) or e.get(k) < 1:
                    errs.append(f"evidence[{i}].{k}: integer >= 1 required")
            if not _is_nonempty_str(e.get("quote")):
                errs.append(f"evidence[{i}].quote: required non-empty string")
    return errs


def finding_from_dict(d: dict[str, Any], lens: str) -> Finding:
    ev = [Evidence(e["file"], int(e["start_line"]), int(e["end_line"]), e["quote"]) for e in d["evidence"]]
    return Finding(
        id=d["id"], area=d["area"], title=d["title"], evidence=ev,
        failure_mechanism=d["failure_mechanism"], trigger=d["trigger"],
        impact_level=d["impact_level"], impact_description=d["impact_description"],
        likelihood=d["likelihood"], detectability=d["detectability"],
        recommendation=d["recommendation"], tradeoff=d["tradeoff"],
        finding_type=d.get("finding_type", "fact"),
        proposed_severity=d.get("proposed_severity", "medium"),
        lenses=[lens],
    )
