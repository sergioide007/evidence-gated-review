"""Build a line-numbered, secret-redacted snapshot of the target. Honest about what was left out."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "target", "build", "dist", ".idea", ".gradle"}
TEXT_EXT = {".py", ".java", ".kt", ".ts", ".tsx", ".js", ".go", ".rs", ".cs", ".rb", ".php", ".sql", ".yaml",
            ".yml", ".json", ".toml", ".xml", ".md", ".txt", ".properties", ".gradle", ".sh", ".tf", ".proto"}
MAX_FILE_BYTES = 200_000

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b(\s*[:=]\s*)([\"']?)[^\s\"']{8,}\3"), r"\1\2[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


_DELIM = re.compile(r"<(/?)file\b", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Make repository content unable to close or forge our `<file>` data blocks.

    A file that contains `</file>` followed by fake instructions would otherwise escape the data block.
    The escape is deterministic and applied identically when rendering and when verifying quotes, so a model
    that copies an escaped line still verifies.
    """
    return _DELIM.sub(lambda m: f"&lt;{m.group(1)}file", text)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # coarse; budgets use provider-reported usage after the call


@dataclass
class Snapshot:
    root: Path
    files: dict[str, list[str]] = field(default_factory=dict)  # relpath -> lines (redacted)
    omitted: list[tuple[str, str]] = field(default_factory=list)  # (relpath, reason)
    truncated: bool = False

    def render(self) -> str:
        """Render as delimited data blocks. Delimiters reduce, but do NOT eliminate, injection risk."""
        out = []
        for path, lines in self.files.items():
            body = "\n".join(f"{i + 1:>5}| {neutralize(ln)}" for i, ln in enumerate(lines))
            out.append(f'<file path="{neutralize(path)}">\n{body}\n</file>')
        return "\n".join(out)

    def digest(self) -> str:
        """SHA-256 over (path, redacted content) of every file in the snapshot: what the models actually saw."""
        h = hashlib.sha256()
        for path in sorted(self.files):
            h.update(path.encode() + b"\0" + "\n".join(self.files[path]).encode() + b"\0")
        return h.hexdigest()

    def tokens(self) -> int:
        return estimate_tokens(self.render())


def build_snapshot(root: Path, max_tokens: int = 120_000) -> Snapshot:
    root = Path(root).resolve()
    snap = Snapshot(root=root)
    budget_chars = max_tokens * 4
    used = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_symlink():
            snap.omitted.append((rel, "symlink"))
            continue
        if p.suffix.lower() not in TEXT_EXT:
            snap.omitted.append((rel, "extension not in allowlist"))
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            snap.omitted.append((rel, "file too large"))
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            snap.omitted.append((rel, "not utf-8"))
            continue
        text = redact(text)
        if used + len(text) > budget_chars:
            snap.omitted.append((rel, "context budget exceeded"))
            snap.truncated = True
            continue
        used += len(text)
        snap.files[rel] = text.split("\n")
    return snap
