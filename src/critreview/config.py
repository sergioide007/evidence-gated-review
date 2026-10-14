"""Configuration (TOML, stdlib tomllib => Python >= 3.11). Model IDs and prices live ONLY here."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .budget import Budget
from .clients import KNOWN_PROVIDERS
from .lenses import LENS_FOCUS
from .models import AREAS, SEVERITIES


@dataclass
class Tier:
    provider: str
    model: str
    max_output_tokens: int = 4000
    price_in_per_mtok: float = 0.0   # fill with CURRENT list prices; we deliberately ship none
    price_out_per_mtok: float = 0.0
    rpm: int | None = None   # provider's requests/minute for this model (free tiers: set it; see config.free.toml)
    tpm: int | None = None   # provider's tokens/minute (input+output) for this model


@dataclass
class Escalation:
    always_escalate_areas: list[str] = field(default_factory=lambda: ["security", "ai_agents"])
    min_severity: str = "high"
    sample_rate_other: float = 0.15
    seed: int = 7


@dataclass
class Config:
    tiers: dict[str, Tier]
    budget: Budget
    escalation: Escalation
    lenses: list[str]
    max_context_tokens: int = 120_000
    report_language: str = "es"
    breaker_failures: int = 3
    breaker_reset_s: float = 30.0
    injection_gate: bool = True
    max_parallel: int = 0           # 0 = one thread per lens; set 1-2 on free tiers
    max_retry_wait_s: float = 8.0   # longest single wait on HTTP 429/5xx (honors Retry-After up to this value)
    retry_attempts: int = 3         # total tries per call on transient errors (429/5xx/timeouts)
    retry_base_delay_s: float = 0.5  # exponential backoff base; free tiers under load (503) need seconds, not ms
    max_limiter_wait_s: float = 120.0  # longest total wait for the per-minute window before giving up


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    tiers = {name: Tier(**t) for name, t in raw["tiers"].items()}
    for name, t in tiers.items():
        if t.provider not in KNOWN_PROVIDERS:
            raise ValueError(f"tiers.{name}.provider '{t.provider}' unknown; known: {', '.join(KNOWN_PROVIDERS)}")
        if not t.model.strip():
            raise ValueError(f"tiers.{name}.model is empty")
        for lim in (t.rpm, t.tpm):
            if lim is not None and lim < 1:
                raise ValueError(f"tiers.{name}: rpm/tpm must be >= 1")
    for required in ("cheap", "mid", "strong"):
        if required not in tiers:
            raise ValueError(f"config must define tiers.{required}")
    b = raw.get("budget", {})
    esc = Escalation(**raw.get("escalation", {}))
    rv = raw.get("review", {})
    lenses = rv.get("lenses", ["reliability", "security", "state_data", "ai_agents"])
    unknown = [ln for ln in lenses if ln not in LENS_FOCUS]
    if unknown or len(set(lenses)) != len(lenses) or not lenses:
        raise ValueError(f"review.lenses invalid (unknown/duplicate/empty): {unknown or lenses}; valid: {sorted(LENS_FOCUS)}")
    rs = raw.get("resilience", {})
    if not 1 <= int(rs.get("retry_attempts", 3)) <= 10 or float(rs.get("retry_base_delay_s", 0.5)) < 0:
        raise ValueError("resilience.retry_attempts must be 1..10 and retry_base_delay_s >= 0")
    if esc.min_severity not in SEVERITIES:
        raise ValueError(f"escalation.min_severity must be one of {SEVERITIES}")
    bad_areas = [a for a in esc.always_escalate_areas if a not in AREAS]
    if bad_areas or not 0.0 <= esc.sample_rate_other <= 1.0:
        raise ValueError(f"escalation invalid: unknown areas {bad_areas} or sample_rate_other outside [0,1]")
    return Config(
        tiers=tiers,
        budget=Budget(**b),
        escalation=esc,
        lenses=lenses,
        injection_gate=bool(rv.get("injection_gate", True)),
        max_parallel=int(rv.get("max_parallel", 0)),
        max_retry_wait_s=float(raw.get("resilience", {}).get("max_retry_wait_s", 8.0)),
        retry_attempts=int(raw.get("resilience", {}).get("retry_attempts", 3)),
        retry_base_delay_s=float(raw.get("resilience", {}).get("retry_base_delay_s", 0.5)),
        max_limiter_wait_s=float(raw.get("resilience", {}).get("max_limiter_wait_s", 120.0)),
        max_context_tokens=rv.get("max_context_tokens", 120_000),
        report_language=rv.get("report_language", "es"),
        breaker_failures=raw.get("resilience", {}).get("breaker_failures", 3),
        breaker_reset_s=raw.get("resilience", {}).get("breaker_reset_s", 30.0),
    )
