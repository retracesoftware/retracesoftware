"""Recording bridge for the gate-based ``System``."""

from retracesoftware.proxy.io import IO
from retracesoftware.proxy.system import System


class Recorder:
    """Bridge a ``System`` recording context to a writer-like object."""

    def __init__(
        self,
        system: System,
        writer,
        *,
        debug: bool = False,
        stacktraces: bool = False,
        on_start=None,
        on_end=None,
    ):
        self.system = system
        self.writer = writer
        self.debug = debug
        self.stacktraces = stacktraces
        self.on_start = on_start
        self.on_end = on_end

    def _stacktrace(self):
        if self.stacktraces:
            return self.writer.stacktrace
        return None

    def _checkpoint(self):
        if self.debug:
            return self.writer.checkpoint
        return None

    def _checkpoint_error(self, checkpoint):
        if checkpoint is None:
            return None

        def checkpoint_error(exc_type, exc_value, exc_tb):
            checkpoint(exc_value)

        return checkpoint_error

    def context(self):
        return IO(self.system, debug=self.debug).writer(
            self.writer,
            stacktraces=self.stacktraces,
            on_start=self.on_start,
            on_end=self.on_end,
        )
