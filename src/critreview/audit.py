"""Tamper-evident audit trail: append-only JSONL where every record carries the SHA-256 of the previous one.

What it gives you: a reproducible account of what the run saw (snapshot digest), asked (prompt/response hashes,
model ids, tokens) and decided (why each finding was accepted or rejected, how severity was computed).
What it does NOT give you: protection against someone who rewrites the WHOLE file and recomputes every hash.
For that, publish the final `chain_head` somewhere the attacker cannot edit (CI log, signed tag, ticket).
Prompts/responses are stored as hashes, not text, so the log does not duplicate the (redacted) source code.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _digest(prev: str, body: dict[str, Any]) -> str:
    return sha256_text(prev + json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.records: list[dict[str, Any]] = []
        self._head = GENESIS
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    @property
    def head(self) -> str:
        return self._head

    def record(self, event: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            body = {"seq": len(self.records), "event": event, "data": data, "prev": self._head}
            body["hash"] = _digest(self._head, {k: v for k, v in body.items() if k != "hash"})
            self._head = body["hash"]
            self.records.append(body)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n")
            return body


def verify_chain(path: Path) -> tuple[bool, str]:
    """Recompute the hash chain. Returns (ok, message). Detects edits, deletions, insertions and reordering."""
    prev = GENESIS
    n = 0
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False, f"record {n}: not valid JSON"
        if rec.get("prev") != prev:
            return False, f"record {n}: broken link (prev hash does not match)"
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("seq") != n - 1 or _digest(prev, body) != rec.get("hash"):
            return False, f"record {n}: content or sequence was altered"
        prev = rec["hash"]
    if n == 0:
        return False, "empty audit log"
    return True, f"{n} records, chain intact, head={prev}"
