"""JSON-lines trace I/O implementation for proxy tests."""

import builtins
import importlib
import json

from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallMarkerMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    ErrorMessage,
    OnStartMessage,
    ResultMessage,
    StacktraceMessage,
    SyncMessage,
    ThreadSwitchMessage,
)


def _write_line(sink, payload):
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    write = getattr(sink, "write", None)
    if write is not None:
        return write(line)
    return sink(line)


def _read_line(source):
    readline = getattr(source, "readline", None)
    if readline is not None:
        return readline()

    read = getattr(source, "read", None)
    if read is not None:
        return read()

    if callable(source):
        return source()

    return next(source)


def _tuple_tree(value):
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


def _encode_error(error):
    return {
        "module": type(error).__module__,
        "name": type(error).__qualname__,
        "args": list(error.args),
    }


def _decode_error(value):
    module_name = value["module"]
    type_name = value["name"]
    if module_name == "builtins":
        error_type = getattr(builtins, type_name)
    else:
        module = importlib.import_module(module_name)
        error_type = module
        for part in type_name.split("."):
            error_type = getattr(error_type, part)
    return error_type(*value.get("args", ()))


class JsonTraceWriter:
    __slots__ = ("_sink",)

    def __init__(self, sink):
        self._sink = sink

    def _write(self, event, **payload):
        return _write_line(self._sink, {"event": event, **payload})

    def on_start(self):
        return self._write("on_start")

    def result(self, value):
        return self._write("result", value=value)

    def error(self, error):
        return self._write("error", error=_encode_error(error))

    def callback(self, fn, args, kwargs):
        return self._write("callback", fn=fn, args=args, kwargs=kwargs)

    def callback_result(self, value):
        return self._write("callback_result", value=value)

    def callback_error(self, error):
        return self._write("callback_error", error=_encode_error(error))

    def checkpoint(self, cursor_delta, value):
        return self._write("checkpoint", cursor_delta=cursor_delta, value=value)

    def stacktrace(self, value):
        return self._write("stacktrace", value=value)

    def thread_switch(self, cursor_delta, thread_id):
        return self._write(
            "thread_switch",
            thread_id=thread_id,
            cursor_delta=cursor_delta,
        )

    def new_binding(self, handle):
        return self._write("new_binding", handle=handle)

    def binding_delete(self, handle):
        return self._write("binding_delete", handle=handle)

    def call_marker(self):
        return self._write("call_marker")

    def sync(self):
        return self._write("sync")


class JsonTraceReader:
    __slots__ = ("_source", "_close")

    def __init__(self, source, *, close=None):
        self._source = source
        self._close = close if close is not None else getattr(source, "close", None)

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        line = _read_line(self._source)
        if line == "":
            raise StopIteration
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        payload = json.loads(line) if isinstance(line, str) else line
        return _message_from_payload(payload)

    read = next

    def close(self):
        if self._close is not None:
            return self._close()


def _message_from_payload(payload):
    event = payload["event"]
    if event == "on_start":
        return OnStartMessage()
    if event == "result":
        return ResultMessage(payload["value"])
    if event == "error":
        return ErrorMessage(_decode_error(payload["error"]))
    if event == "callback":
        return CallbackMessage(
            payload["fn"],
            _tuple_tree(payload["args"]),
            payload["kwargs"],
        )
    if event == "callback_result":
        return CallbackResultMessage(payload["value"])
    if event == "callback_error":
        return CallbackErrorMessage(_decode_error(payload["error"]))
    if event == "checkpoint":
        return CheckpointMessage(
            _tuple_tree(payload["cursor_delta"]),
            payload["value"],
        )
    if event == "stacktrace":
        return StacktraceMessage(_tuple_tree(payload["value"]))
    if event == "thread_switch":
        return ThreadSwitchMessage(
            _tuple_tree(payload["cursor_delta"]),
            thread_id=payload["thread_id"],
        )
    if event == "new_binding":
        return BindOpenMessage(payload["handle"])
    if event == "binding_delete":
        return BindCloseMessage(payload["handle"])
    if event == "call_marker":
        return CallMarkerMessage()
    if event == "sync":
        return SyncMessage()
    raise ValueError(f"unknown JSON trace event: {event!r}")


__all__ = [
    "JsonTraceReader",
    "JsonTraceWriter",
]
