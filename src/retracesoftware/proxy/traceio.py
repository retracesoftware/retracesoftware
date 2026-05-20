"""Semantic trace I/O interfaces for the proxy boundary."""

from typing import Protocol, TypeAlias

from retracesoftware.protocol.messages import (
    CallMessage as _CallMessage,
    CheckpointMessage as _CheckpointMessage,
    ErrorMessage as _ErrorMessage,
    ProtocolMessage,
    ResultMessage as _ResultMessage,
    StacktraceMessage as _StacktraceMessage,
)


class StacktraceMessage(_StacktraceMessage):
    __slots__ = ()


class ResultMessage(_ResultMessage):
    __slots__ = ()


class ErrorMessage(_ErrorMessage):
    __slots__ = ()


class CallMessage(_CallMessage):
    __slots__ = ()


class CheckpointMessage(_CheckpointMessage):
    __slots__ = ("cursor_delta",)

    def __init__(self, cursor_delta, value, *, thread_id=None):
        super().__init__(value, thread_id=thread_id)
        self.cursor_delta = cursor_delta


class OnStartMessage(ProtocolMessage):
    __slots__ = ()


class CallbackMessage(CallMessage):
    __slots__ = ()


class CallbackResultMessage(ResultMessage):
    __slots__ = ()


class CallbackErrorMessage(ErrorMessage):
    __slots__ = ()


class ThreadSwitchMessage(ProtocolMessage):
    __slots__ = ("cursor_delta",)

    def __init__(self, cursor_delta, *, thread_id):
        if thread_id is None:
            raise ValueError("ThreadSwitchMessage requires thread_id")
        super().__init__(thread_id=thread_id)
        self.cursor_delta = cursor_delta


class BindOpenMessage(ProtocolMessage):
    __slots__ = ("handle",)

    def __init__(self, handle):
        super().__init__()
        self.handle = handle


class BindCloseMessage(ProtocolMessage):
    __slots__ = ("handle",)

    def __init__(self, handle):
        super().__init__()
        self.handle = handle


class CallMarkerMessage(ProtocolMessage):
    __slots__ = ()

    def __repr__(self):
        return "CallMarkerMessage"


class SyncMessage(ProtocolMessage):
    __slots__ = ()

    def __repr__(self):
        return "SyncMessage"


TraceMessage: TypeAlias = ProtocolMessage


class TraceReader(Protocol):
    """Read trace messages for replay.

    CONTRACT LOCKED:
    - The reader is callable and returns exactly one TraceMessage per call.
    - This interface has no hidden side channel.
    - Consumers must not probe concrete reader objects for extra capabilities.
    - If replay needs a new behavior, add it explicitly to a public Protocol
      after agreeing the design.
    """

    def __call__(self) -> TraceMessage:
        ...


class PeekableTraceReader(TraceReader, Protocol):
    def peek(self, offset: int = 0) -> TraceMessage:
        ...


class TraceWriter(Protocol):
    def on_start(self) -> None:
        ...

    def result(self, value: object) -> None:
        ...

    def error(self, error: BaseException) -> None:
        ...

    def callback(
        self,
        fn: object,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> None:
        ...

    def callback_result(self, value: object) -> None:
        ...

    def callback_error(self, error: BaseException) -> None:
        ...

    def checkpoint(self, cursor_delta: object, value: object) -> None:
        ...

    def thread_switch(self, cursor_delta: object, thread_id: object) -> None:
        ...

    def new_binding(self, handle: object) -> None:
        ...

    def binding_delete(self, handle: object) -> None:
        ...

    def call_marker(self) -> None:
        ...

    def sync(self) -> None:
        ...


class DefaultTraceWriter:
    __slots__ = ("sink",)

    def __init__(self, sink):
        self.sink = sink

    def _write(self, message):
        write = getattr(self.sink, "write", None)
        if write is not None:
            return write(message)
        return self.sink(message)

    def on_start(self):
        return self._write(OnStartMessage())

    def result(self, value):
        return self._write(ResultMessage(value))

    def error(self, error):
        return self._write(ErrorMessage(error))

    def callback(self, fn, args, kwargs):
        return self._write(CallbackMessage(fn, args, kwargs))

    def callback_result(self, value):
        return self._write(CallbackResultMessage(value))

    def callback_error(self, error):
        return self._write(CallbackErrorMessage(error))

    def checkpoint(self, cursor_delta, value):
        return self._write(CheckpointMessage(cursor_delta, value))

    def stacktrace(self, value):
        return self._write(StacktraceMessage(value))

    def thread_switch(self, cursor_delta, thread_id):
        return self._write(ThreadSwitchMessage(cursor_delta, thread_id=thread_id))

    def new_binding(self, handle):
        return self._write(BindOpenMessage(handle))

    def binding_delete(self, handle):
        return self._write(BindCloseMessage(handle))

    def call_marker(self):
        return self._write(CallMarkerMessage())

    def sync(self):
        return self._write(SyncMessage())


__all__ = [
    "BindCloseMessage",
    "BindOpenMessage",
    "CallMessage",
    "CallMarkerMessage",
    "CallbackErrorMessage",
    "CallbackMessage",
    "CallbackResultMessage",
    "CheckpointMessage",
    "DefaultTraceWriter",
    "ErrorMessage",
    "OnStartMessage",
    "PeekableTraceReader",
    "ProtocolMessage",
    "ResultMessage",
    "StacktraceMessage",
    "SyncMessage",
    "ThreadSwitchMessage",
    "TraceMessage",
    "TraceReader",
    "TraceWriter",
]
