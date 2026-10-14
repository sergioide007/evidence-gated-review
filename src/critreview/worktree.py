"""Ephemeral git worktrees for reviews.

IMPORTANT: a worktree isolates the working tree/HEAD/index only. Hooks, config, refs and objects live in the shared
common dir, and network/secrets/processes are not isolated at all. This is NOT a security boundary: run reviews inside
a container/sandbox with no network and no push credentials. We only harden what git lets us harden cheaply.
"""
from __future__ import annotations

import contextlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                       capture_output=True, text=True, env=env, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def make_read_only(path: Path) -> None:
    """Best effort: remove write bits. Root or a hostile process can undo this; it prevents accidents, not attacks."""
    for p in path.rglob("*"):
        if p.is_symlink():
            continue
        mode = p.stat().st_mode
        p.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(p.stat().st_mode | stat.S_IWUSR)


@contextlib.contextmanager
def ephemeral_worktree(repo: Path, ref: str = "HEAD", readonly: bool = True):
    repo = Path(repo).resolve()
    base = Path(tempfile.mkdtemp(prefix="critreview-wt-"))
    dest = base / "wt"
    _git(repo, "worktree", "add", "--detach", str(dest), ref)
    try:
        if readonly:
            make_read_only(dest)
        yield dest
    finally:
        with contextlib.suppress(Exception):
            make_writable(dest)
        with contextlib.suppress(Exception):
            _git(repo, "worktree", "remove", "--force", str(dest))
        with contextlib.suppress(Exception):
            _git(repo, "worktree", "prune")
        with contextlib.suppress(Exception):
            base.rmdir()
