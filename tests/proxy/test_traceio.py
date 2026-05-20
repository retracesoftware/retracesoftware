import pytest

from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallMarkerMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    DefaultTraceWriter,
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


class WriteSink:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)
        return "written"


def test_default_trace_writer_writes_messages_to_callable_sink():
    messages = []
    writer = DefaultTraceWriter(messages.append)
    error = ValueError("boom")
    stacktrace = (0, ())

    def callback():
        return None

    writer.on_start()
    writer.result("result")
    writer.error(error)
    writer.callback(callback, (1,), {"x": 2})
    writer.signal_callback(callback, (2,), {})
    writer.gc_collect(1)
    writer.callback_result("callback-result")
    writer.callback_error(error)
    writer.checkpoint((0, 4), "main", {"state": "ok"})
    writer.stacktrace(stacktrace)
    writer.thread_switch((0, 3), "worker")
    writer.new_binding(7)
    writer.binding_delete(7)
    writer.call_marker()
    writer.sync()

    assert isinstance(messages[0], OnStartMessage)
    assert isinstance(messages[1], ResultMessage)
    assert messages[1].result == "result"
    assert isinstance(messages[2], ErrorMessage)
    assert messages[2].error is error
    assert isinstance(messages[3], CallbackMessage)
    assert messages[3].fn is callback
    assert messages[3].args == (1,)
    assert messages[3].kwargs == {"x": 2}
    assert isinstance(messages[4], SignalMessage)
    assert messages[4].fn is callback
    assert messages[4].args == (2,)
    assert messages[4].kwargs == {}
    assert isinstance(messages[5], GCMessage)
    assert messages[5].generation == 1
    assert isinstance(messages[6], CallbackResultMessage)
    assert messages[6].result == "callback-result"
    assert isinstance(messages[7], CallbackErrorMessage)
    assert messages[7].error is error
    assert isinstance(messages[8], CheckpointMessage)
    assert messages[8].cursor_delta == (0, 4)
    assert messages[8].thread_id == "main"
    assert messages[8].value == {"state": "ok"}
    assert isinstance(messages[9], StacktraceMessage)
    assert messages[9].stacktrace == stacktrace
    assert isinstance(messages[10], RunToCoordinateMessage)
    assert messages[10].cursor_delta == (0, 3)
    assert isinstance(messages[11], SwitchThreadMessage)
    assert messages[11].thread_id == "worker"
    assert isinstance(messages[12], BindOpenMessage)
    assert messages[12].handle == 7
    assert isinstance(messages[13], BindCloseMessage)
    assert messages[13].handle == 7
    assert isinstance(messages[14], CallMarkerMessage)
    assert isinstance(messages[15], SyncMessage)


def test_default_trace_writer_uses_write_method_sink():
    sink = WriteSink()
    writer = DefaultTraceWriter(sink)

    result = writer.result("value")

    assert result == "written"
    assert isinstance(sink.messages[0], ResultMessage)
    assert sink.messages[0].result == "value"


def test_switch_thread_message_requires_thread_id():
    with pytest.raises(ValueError):
        SwitchThreadMessage(None)
