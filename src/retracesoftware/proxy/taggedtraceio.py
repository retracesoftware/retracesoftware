"""Tagged wire-format implementation of the proxy trace I/O interfaces."""

import retracesoftware.stream as stream
import retracesoftware.functional as functional
from types import SimpleNamespace

from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallMarkerMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    ErrorMessage,
    GCMessage,
    OnStartMessage,
    ResultMessage,
    RunToCoordinateMessage,
    SignalMessage,
    StacktraceMessage,
    SwitchThreadMessage,
    SyncMessage,
    ThreadSwitchMessage,
    _binding_handle,
)


def _read(source):
    next_method = getattr(source, "next", None)
    if next_method is not None:
        return next_method()

    read_method = getattr(source, "read", None)
    if read_method is not None:
        return read_method()

    if callable(source):
        return source()

    return next(source)


def tagged_trace_writer(sink):
    def thread_switch(cursor_delta, thread_id):
        sink("RUN_TO_COORDINATE", cursor_delta)
        return sink("SWITCH_THREAD", thread_id)

    def checkpoint(cursor_delta, thread_id, value):
        return sink("CHECKPOINT", thread_id, cursor_delta, value)

    def signal_callback(fn, args, kwargs):
        return sink("SIGNAL", fn, args, kwargs)

    return SimpleNamespace(
        on_start=functional.partial(sink, "ON_START"),
        result=functional.partial(sink, "RESULT"),
        error=functional.partial(sink, "ERROR"),
        callback=functional.partial(sink, "CALLBACK"),
        signal_callback=signal_callback,
        gc_collect=functional.partial(sink, "GC"),
        callback_result=functional.partial(sink, "CALLBACK_RESULT"),
        callback_error=functional.partial(sink, "CALLBACK_ERROR"),
        checkpoint=checkpoint,
        stacktrace=functional.partial(sink, "STACKTRACE"),
        thread_switch=thread_switch,
        run_to_coordinate=functional.partial(sink, "RUN_TO_COORDINATE"),
        switch_thread=functional.partial(sink, "SWITCH_THREAD"),
        binding_delete=lambda binding: sink("BINDING_DELETE", _binding_handle(binding)),
        call_marker=functional.partial(sink, "CALL"),
        sync=functional.partial(sink, "SYNC"),
    )

class TaggedTraceWriter:
    __slots__ = ("_write",)

    def __init__(self, writer):
        self._write = writer

    def on_start(self):
        return self._write("ON_START")

    def result(self, value):
        return self._write("RESULT", value)

    def error(self, error):
        return self._write("ERROR", error)

    def callback(self, fn, args, kwargs):
        return self._write("CALLBACK", fn, args, kwargs)

    def signal_callback(self, fn, args, kwargs):
        return self._write("SIGNAL", fn, args, kwargs)

    def gc_collect(self, generation):
        return self._write("GC", generation)

    def callback_result(self, value):
        return self._write("CALLBACK_RESULT", value)

    def callback_error(self, error):
        return self._write("CALLBACK_ERROR", error)

    def checkpoint(self, cursor_delta, thread_id, value):
        return self._write("CHECKPOINT", thread_id, cursor_delta, value)

    def stacktrace(self, value):
        return self._write("STACKTRACE", value)

    def thread_switch(self, cursor_delta, thread_id):
        self.run_to_coordinate(cursor_delta)
        return self.switch_thread(thread_id)

    def run_to_coordinate(self, cursor_delta):
        return self._write("RUN_TO_COORDINATE", cursor_delta)

    def switch_thread(self, thread_id):
        return self._write("SWITCH_THREAD", thread_id)

    def binding_delete(self, binding):
        return self._write("BINDING_DELETE", _binding_handle(binding))

    def call_marker(self):
        return self._write("CALL")

    def sync(self):
        return self._write("SYNC")


class TaggedTraceReader:
    __slots__ = ("source", "_close")

    def __init__(self, source, *, close=None):
        self.source = source
        self._close = close if close is not None else getattr(source, "close", None)

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        return next_message(self.source)

    read = next

    def close(self):
        if self._close is not None:
            return self._close()


def next_message(source):
    message_type = _read(source)

    if stream._is_bind_open(message_type):
        return BindOpenMessage(stream._bind_index(message_type))
    if stream._is_bind_close(message_type):
        return BindCloseMessage(stream._bind_index(message_type))
    if message_type == "ON_START":
        return OnStartMessage()
    if message_type == "RESULT":
        return ResultMessage(_read(source))
    if message_type == "ERROR":
        return ErrorMessage(_read(source))
    if message_type == "CALLBACK":
        return CallbackMessage(_read(source), _read(source), _read(source))
    if message_type == "SIGNAL":
        return SignalMessage(_read(source), _read(source), _read(source))
    if message_type == "GC":
        return GCMessage(_read(source))
    if message_type == "CALLBACK_RESULT":
        return CallbackResultMessage(_read(source))
    if message_type == "CALLBACK_ERROR":
        return CallbackErrorMessage(_read(source))
    if message_type == "CALL":
        return CallMarkerMessage()
    if message_type == "SYNC":
        return SyncMessage()
    if message_type == "CHECKPOINT":
        thread_id = _read(source)
        return CheckpointMessage(_read(source), _read(source), thread_id=thread_id)
    if message_type == "STACKTRACE":
        return StacktraceMessage(_read(source))
    if message_type == "RUN_TO_COORDINATE":
        return RunToCoordinateMessage(_read(source))
    if message_type == "SWITCH_THREAD":
        return SwitchThreadMessage(_read(source))
    if message_type == "THREAD_SWITCH":
        thread_id = _read(source)
        return ThreadSwitchMessage(_read(source), thread_id=thread_id)
    if message_type == "NEW_BINDING":
        return BindOpenMessage(_binding_handle(_read(source)))
    if message_type == "BINDING_DELETE":
        return BindCloseMessage(_binding_handle(_read(source)))
    return message_type


__all__ = [
    "TaggedTraceReader",
    "TaggedTraceWriter",
    "tagged_trace_writer",
    "next_message",
]
