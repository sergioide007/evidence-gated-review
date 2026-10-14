"""Minimal .env loader (stdlib only). Never overrides variables already set, never prints values."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    """Load KEY=VALUE lines into os.environ. Returns the NAMES that were set (never the values)."""
    set_names: list[str] = []
    p = Path(path)
    if not p.is_file():
        return set_names
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and val and key not in os.environ:
            os.environ[key] = val
            set_names.append(key)
    return set_names
