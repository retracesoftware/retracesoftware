"""Record mode for the gate-based proxy system."""

from retracesoftware.proxy.contexts import record_context

from .base import Mode


class RecordMode(Mode):
    """Install recording handlers onto a ``System``."""

    def __init__(self, system, writer, *, debug=False, stacktraces=False, on_start=None, on_end=None):
        super().__init__(system, on_start=on_start, on_end=on_end)
        self.writer = writer
        self.debug = debug
        self.stacktraces = stacktraces

    def context(self):
        return record_context(
            self.system,
            self.writer,
            debug=self.debug,
            stacktraces=self.stacktraces,
            on_start=self.on_start,
            on_end=self.on_end,
        )
