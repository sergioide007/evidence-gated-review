"""Deterministic prompt-injection signal scan and coverage gate.

Why this exists: a lens that OBEYS an injected comment can answer with a perfectly valid `{"findings": []}`.
Schema checks cannot tell that apart from a genuinely clean review. So, independently of any model, we scan the
snapshot for known injection phrasings and require that each hit is *reported* by at least one verified finding.
A hit nobody reported means the review is incomplete: absence of findings cannot be trusted.

Limits (stated on purpose): this is a phrase heuristic. Paraphrased, encoded or multilingual-obfuscated
injections can evade it. It raises the cost of the cheapest attack; it does not solve prompt injection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Finding
from .normalizer import Snapshot

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore-instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|any|your|these)\b.{0,30}"
        r"\b(instructions?|prompts?|rules?|guidelines?)\b", re.I)),
    ("ignora-instrucciones", re.compile(
        r"\b(ignora|ignore|olvida|omite|descarta)\b.{0,40}\b(todas?|las|tus|anteriores|previas)\b.{0,30}"
        r"\b(instrucciones|reglas|indicaciones)\b", re.I)),
    ("system-override", re.compile(r"\b(system|admin(istrator)?)\s*(override|prompt|message)\b", re.I)),
    ("note-to-reviewer", re.compile(
        r"\b(note|message|instruction)s?\s+(to|for)\s+(the\s+)?(automated\s+)?(reviewers?|auditors?|ai|llms?|"
        r"assistants?|models?|agents?)\b", re.I)),
    ("suppress-findings", re.compile(
        r"\b(report|say|state|conclude|respond|answer)\b.{0,40}\b(no|zero|0)\s+(findings?|issues?|problems?|"
        r"vulnerabilit(y|ies)|hallazgos|problemas)\b", re.I)),
    ("suprimir-hallazgos", re.compile(
        r"\b(reporta|di|indica|responde|concluye)\b.{0,40}\b(sin|no\s+hay|cero|ning[uú]n)\s+(hallazgos?|problemas?|"
        r"vulnerabilidades)\b", re.I)),
    ("do-not-report", re.compile(r"\b(do\s*n['o]?t|never|no)\s+(report|flag|mention|include)\b", re.I)),
]

COVER_TOLERANCE = 1  # a finding covers a signal if one of its evidence ranges touches the signal line +/- 1


@dataclass(frozen=True)
class InjectionSignal:
    file: str
    line: int
    pattern: str
    excerpt: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "pattern": self.pattern, "excerpt": self.excerpt}


def scan(snap: Snapshot) -> list[InjectionSignal]:
    out: list[InjectionSignal] = []
    for path, lines in snap.files.items():
        for i, ln in enumerate(lines, start=1):
            for name, pat in _PATTERNS:
                if pat.search(ln):
                    out.append(InjectionSignal(path, i, name, ln.strip()[:160]))
                    break  # one signal per line is enough
    return out


def uncovered(signals: list[InjectionSignal], findings: list[Finding]) -> list[InjectionSignal]:
    """Signals that no VERIFIED finding points at."""
    def covered(sig: InjectionSignal) -> bool:
        return any(e.file == sig.file
                   and e.start_line - COVER_TOLERANCE <= sig.line <= e.end_line + COVER_TOLERANCE
                   for f in findings if f.verified for e in f.evidence)
    return [s for s in signals if not covered(s)]
