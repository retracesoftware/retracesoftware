"""Replay mode for the gate-based proxy system."""

from retracesoftware.proxy.contexts import replay_context

from .base import Mode


class ReplayMode(Mode):
    """Install replay handlers onto a ``System``."""

    def __init__(self, system, reader, *, normalize=None, on_start=None, on_end=None):
        super().__init__(system, on_start=on_start, on_end=on_end)
        self.reader = reader
        self.normalize = normalize

    def context(self):
        return replay_context(
            self.system,
            self.reader,
            normalize=self.normalize,
            on_start=self.on_start,
            on_end=self.on_end,
        )
