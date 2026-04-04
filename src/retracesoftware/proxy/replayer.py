"""Replay bridge for the gate-based ``System``."""

from retracesoftware.proxy.io import IO
from retracesoftware.proxy.system import System


class Replayer:
    """Bridge a ``System`` replay context to a reader-like object."""

    def __init__(
        self,
        system: System,
        reader,
        *,
        normalize=None,
        on_start=None,
        on_end=None,
    ):
        self.system = system
        self.reader = reader
        self.normalize = normalize
        self.on_start = on_start
        self.on_end = on_end

    def _checkpoint(self):
        if self.normalize is None:
            return None
        return functional.sequence(self.normalize, self.reader.checkpoint)

    @staticmethod
    def _checkpoint_error(checkpoint):
        if checkpoint is None:
            return None

        def checkpoint_error(exc_type, exc_value, exc_tb):
            checkpoint(exc_value)

        return checkpoint_error

    def _configure_reader(self):
        reader = self.reader
        system = self.system

        if hasattr(reader, "stub_factory"):
            reader.stub_factory = system.disable_for(reader.stub_factory)

        if hasattr(reader, "_mark_retraced"):
            reader._mark_retraced = system.is_bound.add

        stream = getattr(reader, "_stream", None)
        if stream is not None and hasattr(stream, "_mark_retraced"):
            stream._mark_retraced = system.is_bound.add
        if stream is not None and hasattr(stream, "stub_factory"):
            stream.stub_factory = system.disable_for(stream.stub_factory)

        native_reader = getattr(reader, "_native_reader", None)
        if native_reader is None:
            source_reader = getattr(reader, "source", None)
            if source_reader is not None and hasattr(source_reader, "peek"):
                native_reader = source_reader
            else:
                native_reader = reader

        if hasattr(native_reader, "stub_factory"):
            native_reader.stub_factory = system.disable_for(native_reader.stub_factory)

    def context(self):
        return IO(self.system, normalize=self.normalize).reader(
            self.reader,
            on_start=self.on_start,
            on_end=self.on_end,
        )
