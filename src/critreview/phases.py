"""Explicit run phases with legal transitions.

A tiny finite-state machine over the pipeline. Scope, stated honestly: it runs inside ONE process and protects
the *ordering invariants* of a review (e.g. you cannot compute a verdict before verifying evidence). It is not a
distributed-consensus mechanism and makes no claim about network partitions.
"""
from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    INIT = "init"
    SNAPSHOT = "snapshot"
    LENSES = "lenses"
    VERIFY = "verify"
    ESCALATE = "escalate"
    VERDICT = "verdict"
    DONE = "done"
    ABORTED = "aborted"


_LEGAL: dict[Phase, set[Phase]] = {
    Phase.INIT: {Phase.SNAPSHOT, Phase.ABORTED},
    Phase.SNAPSHOT: {Phase.LENSES, Phase.ABORTED},
    Phase.LENSES: {Phase.VERIFY, Phase.ABORTED},
    Phase.VERIFY: {Phase.ESCALATE, Phase.ABORTED},
    Phase.ESCALATE: {Phase.VERDICT, Phase.ABORTED},
    Phase.VERDICT: {Phase.DONE, Phase.ABORTED},
    Phase.DONE: set(),
    Phase.ABORTED: set(),
}


class IllegalTransition(RuntimeError):
    pass


class PhaseMachine:
    def __init__(self, on_transition=None):
        self.phase = Phase.INIT
        self._on = on_transition

    def advance(self, to: Phase) -> None:
        if to not in _LEGAL[self.phase]:
            raise IllegalTransition(f"{self.phase.value} -> {to.value} is not allowed")
        prev, self.phase = self.phase, to
        if self._on:
            self._on(prev, to)
