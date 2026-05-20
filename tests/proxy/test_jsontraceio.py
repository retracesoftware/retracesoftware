from io import StringIO
import json

import pytest

from retracesoftware.proxy.jsontraceio import JsonTraceReader, JsonTraceWriter
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


def test_json_trace_writer_emits_json_lines():
    sink = StringIO()
    writer = JsonTraceWriter(sink)

    writer.on_start()
    writer.result({"ok": True})
    writer.checkpoint((0, 2), {"state": "ok"})
    writer.thread_switch((0, 3), "worker")
    writer.new_binding(7)
    writer.sync()

    assert [json.loads(line) for line in sink.getvalue().splitlines()] == [
        {"event": "on_start"},
        {"event": "result", "value": {"ok": True}},
        {"event": "checkpoint", "cursor_delta": [0, 2], "value": {"state": "ok"}},
        {"event": "thread_switch", "thread_id": "worker", "cursor_delta": [0, 3]},
        {"event": "new_binding", "handle": 7},
        {"event": "sync"},
    ]


def test_json_trace_reader_decodes_json_lines_to_messages():
    source = StringIO(
        "\n".join(
            json.dumps(payload)
            for payload in [
                {"event": "on_start"},
                {"event": "result", "value": "result"},
                {
                    "event": "error",
                    "error": {
                        "module": "builtins",
                        "name": "ValueError",
                        "args": ["boom"],
                    },
                },
                {
                    "event": "callback",
                    "fn": "callback",
                    "args": [1],
                    "kwargs": {"x": 2},
                },
                {"event": "callback_result", "value": "callback-result"},
                {
                    "event": "callback_error",
                    "error": {
                        "module": "builtins",
                        "name": "RuntimeError",
                        "args": ["callback boom"],
                    },
                },
                {
                    "event": "checkpoint",
                    "cursor_delta": [0, 4],
                    "value": {"state": "ok"},
                },
                {"event": "stacktrace", "value": [0, []]},
                {
                    "event": "thread_switch",
                    "thread_id": "worker",
                    "cursor_delta": [0, 3],
                },
                {"event": "new_binding", "handle": 7},
                {"event": "binding_delete", "handle": 7},
                {"event": "call_marker"},
                {"event": "sync"},
            ]
        )
        + "\n"
    )
    reader = JsonTraceReader(source)

    assert isinstance(reader.next(), OnStartMessage)

    result = reader.next()
    assert isinstance(result, ResultMessage)
    assert result.result == "result"

    failure = reader.next()
    assert isinstance(failure, ErrorMessage)
    assert isinstance(failure.error, ValueError)
    assert failure.error.args == ("boom",)

    call = reader.next()
    assert isinstance(call, CallbackMessage)
    assert call.fn == "callback"
    assert call.args == (1,)
    assert call.kwargs == {"x": 2}

    callback_result = reader.next()
    assert isinstance(callback_result, CallbackResultMessage)
    assert callback_result.result == "callback-result"

    callback_failure = reader.next()
    assert isinstance(callback_failure, CallbackErrorMessage)
    assert isinstance(callback_failure.error, RuntimeError)
    assert callback_failure.error.args == ("callback boom",)

    checkpoint = reader.next()
    assert isinstance(checkpoint, CheckpointMessage)
    assert checkpoint.cursor_delta == (0, 4)
    assert checkpoint.value == {"state": "ok"}

    stacktrace = reader.next()
    assert isinstance(stacktrace, StacktraceMessage)
    assert stacktrace.stacktrace == (0, ())

    switch = reader.next()
    assert isinstance(switch, ThreadSwitchMessage)
    assert switch.thread_id == "worker"
    assert switch.cursor_delta == (0, 3)

    bind_open = reader.next()
    assert isinstance(bind_open, BindOpenMessage)
    assert bind_open.handle == 7

    bind_close = reader.next()
    assert isinstance(bind_close, BindCloseMessage)
    assert bind_close.handle == 7

    assert isinstance(reader.next(), CallMarkerMessage)
    assert isinstance(reader.next(), SyncMessage)

    with pytest.raises(StopIteration):
        reader.next()


def test_json_trace_writer_and_reader_round_trip_json_compatible_messages():
    sink = StringIO()
    writer = JsonTraceWriter(sink)

    writer.on_start()
    writer.result([1, 2, 3])
    writer.callback("callback", ("arg",), {"flag": True})
    writer.callback_result(None)
    writer.checkpoint((0, 6), {"state": ["ok"]})
    writer.thread_switch((0, 8), "worker")
    writer.binding_delete(3)

    sink.seek(0)
    reader = JsonTraceReader(sink)

    assert isinstance(reader.next(), OnStartMessage)
    assert reader.next().result == [1, 2, 3]

    callback = reader.next()
    assert callback.fn == "callback"
    assert callback.args == ("arg",)
    assert callback.kwargs == {"flag": True}

    assert reader.next().result is None
    checkpoint = reader.next()
    assert checkpoint.cursor_delta == (0, 6)
    assert checkpoint.value == {"state": ["ok"]}

    switch = reader.next()
    assert switch.thread_id == "worker"
    assert switch.cursor_delta == (0, 8)

    close = reader.next()
    assert close.handle == 3
