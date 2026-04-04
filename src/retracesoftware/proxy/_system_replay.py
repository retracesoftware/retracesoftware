"""Replay-mode shim."""

from .mode.replay import ReplayMode


def replay_context(system, reader, normalize=None, on_start=None, on_end=None):
    return ReplayMode(
        system,
        reader,
        normalize=normalize,
        on_start=on_start,
        on_end=on_end,
    ).context()

__all__ = ["replay_context"]
