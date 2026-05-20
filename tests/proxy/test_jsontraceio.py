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
    GCMessage,
    OnStartMessage,
    ResultMessage,
    RunToCoordinateMessage,
    SignalMessage,
    StacktraceMessage,
    SwitchThreadMessage,
    SyncMessage,
)


def test_json_trace_writer_emits_json_lines():
    sink = StringIO()
    writer = JsonTraceWriter(sink)

    writer.on_start()
    writer.result({"ok": True})
    writer.signal_callback("handler", (2,), {})
    writer.gc_collect(1)
    writer.checkpoint((0, 2), "main", {"state": "ok"})
    writer.thread_switch((0, 3), "worker")
    writer.new_binding(7)
    writer.sync()

    assert [json.loads(line) for line in sink.getvalue().splitlines()] == [
        {"event": "on_start"},
        {"event": "result", "value": {"ok": True}},
        {
            "args": [2],
            "event": "signal",
            "fn": "handler",
            "kwargs": {},
        },
        {"event": "gc", "generation": 1},
        {
            "event": "checkpoint",
            "cursor_delta": [0, 2],
            "thread_id": "main",
            "value": {"state": "ok"},
        },
        {"event": "run_to_coordinate", "cursor_delta": [0, 3]},
        {"event": "switch_thread", "thread_id": "worker"},
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
                {
                    "event": "signal",
                    "fn": "handler",
                    "args": [2],
                    "kwargs": {},
                },
                {"event": "gc", "generation": 1},
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
                    "thread_id": "main",
                    "value": {"state": "ok"},
                },
                {"event": "stacktrace", "value": [0, []]},
                {
                    "event": "run_to_coordinate",
                    "cursor_delta": [0, 3],
                },
                {"event": "switch_thread", "thread_id": "worker"},
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

    signal = reader.next()
    assert isinstance(signal, SignalMessage)
    assert signal.fn == "handler"
    assert signal.args == (2,)
    assert signal.kwargs == {}

    gc_message = reader.next()
    assert isinstance(gc_message, GCMessage)
    assert gc_message.generation == 1

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
    assert checkpoint.thread_id == "main"
    assert checkpoint.value == {"state": "ok"}

    stacktrace = reader.next()
    assert isinstance(stacktrace, StacktraceMessage)
    assert stacktrace.stacktrace == (0, ())

    run_to = reader.next()
    assert isinstance(run_to, RunToCoordinateMessage)
    assert run_to.cursor_delta == (0, 3)

    switch = reader.next()
    assert isinstance(switch, SwitchThreadMessage)
    assert switch.thread_id == "worker"

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
    writer.signal_callback("handler", (2,), {})
    writer.gc_collect(1)
    writer.callback_result(None)
    writer.checkpoint((0, 6), "main", {"state": ["ok"]})
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

    signal = reader.next()
    assert signal.fn == "handler"
    assert signal.args == (2,)
    assert signal.kwargs == {}

    gc_message = reader.next()
    assert gc_message.generation == 1

    assert reader.next().result is None
    checkpoint = reader.next()
    assert checkpoint.cursor_delta == (0, 6)
    assert checkpoint.thread_id == "main"
    assert checkpoint.value == {"state": ["ok"]}

    run_to = reader.next()
    assert run_to.cursor_delta == (0, 8)

    switch = reader.next()
    assert switch.thread_id == "worker"

    close = reader.next()
    assert close.handle == 3
