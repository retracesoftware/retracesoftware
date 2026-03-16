"""
Abstract writer/reader protocols for record and replay.

These define the interface that ``System.record_context`` (writer) and
``System.replay_context`` (reader) require.  Every concrete backend
(stream-based, in-memory, etc.) implements these.

The recording is an ordered sequence of events:

    result      — return value of an external call
    error       — exception raised by an external call
    checkpoint  — normalised value for divergence detection

Additional hooks (bind, write_call, sync) let the writer
track object identity and synchronise with the underlying transport.
"""
from abc import ABC, abstractmethod


class Writer(ABC):
    """Abstract writer — records events during ``record_context``.

    Concrete subclasses must implement every abstract method.
    ``checkpoint`` is optional; provide it when the ``normalize``
    parameter of ``record_context`` is used.
    """

    @abstractmethod
    def bind(self, *a, **kw):
        """Notify that a patched object has entered the sandbox."""

    @abstractmethod
    def write_call(self, *a, **kw):
        """Record an internal callback invocation (ext→int)."""

    @abstractmethod
    def sync(self):
        """Write a synchronisation point for the current thread.

        The tracefile is shared across many threads.  Before an
        external call executes, ``sync`` writes a small marker message
        so the reader can later locate this thread's position in the
        stream and deliver the correct result back to it.
        """

    @abstractmethod
    def write_result(self, *a, **kw):
        """Record the return value of an external call."""

    @abstractmethod
    def write_passthrough_result(self, *a, **kw):
        """Record the return value of an external call."""

    @abstractmethod
    def write_error(self, *a, **kw):
        """Record an exception raised by an external call."""

    def checkpoint(self, value):
        """Store a normalised value for divergence detection.

        Only called when ``normalize`` is passed to
        ``record_context``.  The default is a no-op; override when
        checkpoint support is needed.
        """

    def stacktrace(self):
        """Record a stack trace at the current call boundary.

        Only called when ``stacktraces=True`` is passed to
        ``record_context``.  The default is a no-op; override when
        stack trace recording is needed.

        The implementation owns the stack-capture strategy (e.g.
        ``StackFactory.delta()``) and the serialisation format.
        The proxy system simply calls ``writer.stacktrace()`` at
        each call boundary — it knows nothing about deltas, handles,
        or frames.
        """


class Reader(ABC):
    """Abstract reader — replays events during ``replay_context``.

    Concrete subclasses must implement every abstract method.
    ``checkpoint`` is optional; provide it when the ``normalize``
    parameter of ``replay_context`` is used.
    """

    @abstractmethod
    def bind(self, *a, **kw):
        """Notify that a patched object has entered the sandbox."""

    @abstractmethod
    def sync(self):
        """Advance to the next synchronisation point for this thread.

        During replay the tracefile contains interleaved messages from
        many threads.  ``sync`` reads forward until it reaches the
        marker that was written by the corresponding ``Writer.sync``,
        ensuring the next ``read_result`` returns the value that
        belongs to this thread.
        """

    @abstractmethod
    def read_result(self):
        """Return the next recorded value.

        If the recorded event was an error, the implementation should
        raise the stored exception.
        """

    def checkpoint(self, value):
        """Compare *value* against the stored checkpoint.

        Only called when ``normalize`` is passed to
        ``replay_context``.  The default is a no-op; override when
        checkpoint support is needed.
        """

