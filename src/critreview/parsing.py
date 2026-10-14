"""Extract the structured block from a model answer.

Two-phase output protocol: the model reasons in free text first, then emits ONE JSON object last.
(Strict JSON-only output has been reported to degrade reasoning: Tam et al., EMNLP 2024 Industry.)
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _balanced_objects(text: str):
    """Yield every top-level {...} substring, string/escape aware."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:i + 1]
                start = -1


def extract_last_json(text: str) -> dict[str, Any] | None:
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    candidates += list(_balanced_objects(text))
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def parse_lens_output(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (raw finding dicts, errors). Errors non-empty => the lens output is NOT trustworthy."""
    obj = extract_last_json(text)
    if obj is None:
        return [], ["no JSON object found in model output"]
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return [], ["JSON object has no 'findings' array"]
    return findings, []
