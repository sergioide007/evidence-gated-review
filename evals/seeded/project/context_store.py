"""Shared context between agents."""

SHARED = {}  # mutable dict used by every agent; lives only in process memory


def snapshot():
    return dict(SHARED)
