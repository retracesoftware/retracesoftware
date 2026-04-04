"""
Abstract writer/reader protocols for record and replay.

These define the interface that ``System.record_context`` (writer) and
``System.replay_context`` (reader) require.  Every concrete backend
(stream-based, in-memory, etc.) implements these.

The recording is an ordered sequence of events:

    result      — return value of an external call
    error       — exception raised by an external call
    checkpoint  — normalised value for divergence detection

Additional hooks (bind, async_call, write_call, sync) let the writer
track object identity, mark external call boundaries, and synchronise
with the underlying transport.
"""
from abc import ABC, abstractmethod
from typing import Any, MutableMapping, Protocol, runtime_checkable


@runtime_checkable
class BindableProtocol(Protocol):
    def bind(self, obj: object) -> Any:
        ...


@runtime_checkable
class InterningWriterProtocol(BindableProtocol, Protocol):
    def intern(self, obj: object) -> Any:
        ...


@runtime_checkable
class AsyncNewPatchedWriterProtocol(Protocol):
    def async_new_patched(self, obj: object) -> Any:
        ...


@runtime_checkable
class ResultWriterProtocol(Protocol):
    def write_result(self, value: object) -> None:
        ...

    def write_error(self, exc_type, exc_value, exc_tb) -> None:
        ...


@runtime_checkable
class AsyncCallWriterProtocol(Protocol):
    def async_call(self, *args, **kwargs) -> None:
        ...


@runtime_checkable
class SyncProtocol(Protocol):
    def sync(self) -> None:
        ...


@runtime_checkable
class CallStartProtocol(Protocol):
    def write_call(self, *args, **kwargs) -> None:
        ...


@runtime_checkable
class CheckpointProtocol(Protocol):
    def checkpoint(self, value: object) -> None:
        ...


@runtime_checkable
class StacktraceWriterProtocol(Protocol):
    def stacktrace(self) -> None:
        ...


@runtime_checkable
class TypeSerializerWriterProtocol(Protocol):
    type_serializer: MutableMapping[type, Any]


@runtime_checkable
class TypeDeserializerReaderProtocol(Protocol):
    type_deserializer: MutableMapping[type, Any]


@runtime_checkable
class StubFactoryReaderProtocol(BindableProtocol, Protocol):
    def stub_factory(self, cls: type) -> object:
        ...


@runtime_checkable
class ReaderProtocol(BindableProtocol, SyncProtocol, CallStartProtocol, Protocol):
    def read_result(self) -> object:
        ...


@runtime_checkable
class WriterProtocol(
    InterningWriterProtocol,
    AsyncNewPatchedWriterProtocol,
    ResultWriterProtocol,
    AsyncCallWriterProtocol,
    CallStartProtocol,
    SyncProtocol,
    TypeSerializerWriterProtocol,
    Protocol,
):
    pass


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
    def async_call(self, *a, **kw):
        """Record an internal callback invocation (ext→int)."""

    @abstractmethod
    def sync(self):
        """Write a synchronisation point for the current thread.

        The tracefile is shared across many threads.  Before an
        thread handoff or other explicit synchronisation step,
        ``sync`` writes a small marker message so the reader can later
        locate this thread's position in the stream.
        """

    @abstractmethod
    def write_call(self, *args, **kwargs):
        """Write the start marker for an external call.

        ``System.record_context`` emits this immediately before an
        external call executes so replay can align the subsequent
        recorded result with the correct live call site. Implementations
        should accept and ignore any observed call arguments.
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
        marker that was written by the corresponding ``Writer.sync``.
        """

    @abstractmethod
    def write_call(self, *args, **kwargs):
        """Advance to the next external-call boundary marker.

        During replay, ``write_call`` aligns the next
        ``read_result`` with the corresponding live external call.
        Implementations should accept and ignore any observed call
        arguments.
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
