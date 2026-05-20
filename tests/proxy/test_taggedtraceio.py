import retracesoftware.stream as stream

from retracesoftware.proxy.taggedtraceio import (
    TaggedTraceReader,
    TaggedTraceWriter,
    tagged_trace_writer,
    next_message,
)
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


def read_from(values):
    return iter(values).__next__


def test_tagged_trace_writer_methods_emit_wire_tags():
    writes = []
    writer = TaggedTraceWriter(lambda *values: writes.append(values))
    error = ValueError("boom")
    stacktrace = (0, ())

    def callback():
        return None

    writer.on_start()
    writer.result("result")
    writer.error(error)
    writer.callback(callback, (1,), {"x": 2})
    writer.callback_result("callback-result")
    writer.callback_error(error)
    writer.checkpoint((0, 4), {"state": "ok"})
    writer.stacktrace(stacktrace)
    writer.thread_switch((0, 3), "worker")
    writer.new_binding(7)
    writer.binding_delete(7)
    writer.call_marker()
    writer.sync()

    assert writes == [
        ("ON_START",),
        ("RESULT", "result"),
        ("ERROR", error),
        ("CALLBACK", callback, (1,), {"x": 2}),
        ("CALLBACK_RESULT", "callback-result"),
        ("CALLBACK_ERROR", error),
        ("CHECKPOINT", (0, 4), {"state": "ok"}),
        ("STACKTRACE", stacktrace),
        ("THREAD_SWITCH", "worker", (0, 3)),
        ("NEW_BINDING", 7),
        ("BINDING_DELETE", 7),
        ("CALL",),
        ("SYNC",),
    ]


def test_tagged_trace_writer_function_methods_emit_wire_tags():
    writes = []
    writer = tagged_trace_writer(lambda *values: writes.append(values))
    error = ValueError("boom")
    stacktrace = (0, ())

    def callback():
        return None

    writer.on_start()
    writer.result("result")
    writer.error(error)
    writer.callback(callback, (1,), {"x": 2})
    writer.callback_result("callback-result")
    writer.callback_error(error)
    writer.checkpoint((0, 4), {"state": "ok"})
    writer.stacktrace(stacktrace)
    writer.thread_switch((0, 3), "worker")
    writer.new_binding(7)
    writer.binding_delete(7)
    writer.call_marker()
    writer.sync()

    assert writes == [
        ("ON_START",),
        ("RESULT", "result"),
        ("ERROR", error),
        ("CALLBACK", callback, (1,), {"x": 2}),
        ("CALLBACK_RESULT", "callback-result"),
        ("CALLBACK_ERROR", error),
        ("CHECKPOINT", (0, 4), {"state": "ok"}),
        ("STACKTRACE", stacktrace),
        ("THREAD_SWITCH", "worker", (0, 3)),
        ("NEW_BINDING", 7),
        ("BINDING_DELETE", 7),
        ("CALL",),
        ("SYNC",),
    ]


def test_tagged_trace_reader_decodes_wire_tags_to_messages():
    error = ValueError("boom")

    def callback():
        return None

    reader = TaggedTraceReader(read_from([
        "ON_START",
        "RESULT",
        "result",
        "ERROR",
        error,
        "CALLBACK",
        callback,
        (1,),
        {"x": 2},
        "CALLBACK_RESULT",
        "callback-result",
        "CALLBACK_ERROR",
        error,
        "CHECKPOINT",
        (0, 4),
        {"state": "ok"},
        "STACKTRACE",
        (0, ()),
        "THREAD_SWITCH",
        "worker",
        (0, 3),
        "NEW_BINDING",
        stream.Binding(7),
        "BINDING_DELETE",
        stream.Binding(7),
        "CALL",
        "SYNC",
    ]))

    assert isinstance(reader.next(), OnStartMessage)

    result = reader.next()
    assert isinstance(result, ResultMessage)
    assert result.result == "result"

    failure = reader.next()
    assert isinstance(failure, ErrorMessage)
    assert failure.error is error

    call = reader.next()
    assert isinstance(call, CallbackMessage)
    assert call.fn is callback
    assert call.args == (1,)
    assert call.kwargs == {"x": 2}

    callback_result = reader.next()
    assert isinstance(callback_result, CallbackResultMessage)
    assert callback_result.result == "callback-result"

    callback_failure = reader.next()
    assert isinstance(callback_failure, CallbackErrorMessage)
    assert callback_failure.error is error

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


def test_next_message_decodes_native_binding_markers_and_unknown_tags():
    opened = next_message(read_from([("__bind__", 7)]))
    closed = next_message(read_from([("__unbind__", 7)]))

    assert isinstance(opened, BindOpenMessage)
    assert opened.handle == 7
    assert isinstance(closed, BindCloseMessage)
    assert closed.handle == 7
    assert next_message(read_from(["UNKNOWN"])) == "UNKNOWN"
