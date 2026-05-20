import pytest
import retracesoftware.stream as stream

from retracesoftware.proxy.traceio import (
    CallMarkerMessage,
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    ResultMessage,
    StacktraceMessage,
    BindCloseMessage,
    BindOpenMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    OnStartMessage,
    SyncMessage,
    ThreadSwitchMessage,
)
from retracesoftware.proxy.messagestream import (
    BindingStream,
    CallbackStream,
    ExpectedBindMarker,
    MessageStream,
    PeekableStream,
    SchedulerStream,
    next_message,
)


def read_from(values):
    return iter(values).__next__


def test_next_message_reads_result():
    message = next_message(read_from(["RESULT", 42]))

    assert isinstance(message, ResultMessage)
    assert message.result == 42


def test_next_message_reads_error():
    error = ValueError("boom")
    message = next_message(read_from(["ERROR", error]))

    assert isinstance(message, ErrorMessage)
    assert message.error is error


def test_next_message_reads_callback():
    def callback():
        return None

    message = next_message(read_from(["CALLBACK", callback, (1,), {"x": 2}]))

    assert isinstance(message, CallbackMessage)
    assert isinstance(message, CallMessage)
    assert message.fn is callback
    assert message.args == (1,)
    assert message.kwargs == {"x": 2}


def test_next_message_reads_callback_completion():
    result = next_message(read_from(["CALLBACK_RESULT", "ok"]))
    error = RuntimeError("nope")
    failure = next_message(read_from(["CALLBACK_ERROR", error]))

    assert isinstance(result, CallbackResultMessage)
    assert isinstance(result, ResultMessage)
    assert result.result == "ok"
    assert isinstance(failure, CallbackErrorMessage)
    assert isinstance(failure, ErrorMessage)
    assert failure.error is error


def test_next_message_reads_checkpoint_and_stacktrace():
    checkpoint = next_message(read_from(["CHECKPOINT", {"state": "ok"}]))
    stacktrace = next_message(read_from(["STACKTRACE", (0, ())]))

    assert isinstance(checkpoint, CheckpointMessage)
    assert checkpoint.value == {"state": "ok"}
    assert isinstance(stacktrace, StacktraceMessage)
    assert stacktrace.stacktrace == (0, ())


def test_next_message_passes_unknown_tags_through():
    assert next_message(read_from(["UNKNOWN"])) == "UNKNOWN"


def test_next_message_reads_control_markers():
    assert isinstance(next_message(read_from(["CALL"])), CallMarkerMessage)
    assert isinstance(next_message(read_from(["SYNC"])), SyncMessage)


def test_next_message_reads_lifecycle_message():
    assert isinstance(next_message(read_from(["ON_START"])), OnStartMessage)


def test_next_message_reads_thread_switch():
    message = next_message(read_from(["THREAD_SWITCH", "worker", (1, 2, 3)]))

    assert isinstance(message, ThreadSwitchMessage)
    assert message.thread_id == "worker"
    assert message.cursor_delta == (1, 2, 3)


def test_next_message_reads_binding_messages():
    opened = next_message(read_from(["NEW_BINDING", 7]))
    closed = next_message(read_from(["BINDING_DELETE", stream.Binding(7)]))

    assert isinstance(opened, BindOpenMessage)
    assert opened.handle == 7
    assert isinstance(closed, BindCloseMessage)
    assert closed.handle == 7


def test_peekable_stream_peek_does_not_consume():
    stream = PeekableStream(read_from(["RESULT", 7]))

    assert stream.peek() == "RESULT"
    assert stream.peek() == "RESULT"

    message = next_message(stream.next)

    assert isinstance(message, ResultMessage)
    assert message.result == 7


def test_message_stream_peeks_complete_messages():
    stream = PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (1, 2, 3),
        "RESULT",
        10,
    ])))

    message = stream.peek()

    assert stream.peek() is message
    assert stream.next() is message
    assert isinstance(message, ThreadSwitchMessage)
    assert message.thread_id == "worker"
    assert message.cursor_delta == (1, 2, 3)

    result = stream.next()

    assert isinstance(result, ResultMessage)
    assert result.result == 10


def test_binding_stream_bind_resolves_bound_refs():
    source = PeekableStream(MessageStream(read_from([
        "NEW_BINDING",
        7,
        "RESULT",
        {"value": stream.Binding(7)},
    ])))
    messages = BindingStream(source)
    resolved = object()

    messages.bind(resolved)
    message = messages.next()

    assert isinstance(message, ResultMessage)
    assert message.result == {"value": resolved}


def test_binding_stream_does_not_surface_bind_open():
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "NEW_BINDING",
        7,
    ]))))

    with pytest.raises(RuntimeError, match="bind marker returned"):
        messages.next()


def test_binding_stream_bind_requires_next_bind_open():
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "RESULT",
        5,
    ]))))

    with pytest.raises(ExpectedBindMarker) as excinfo:
        messages.bind(object())

    assert isinstance(excinfo.value.next, ResultMessage)
    assert excinfo.value.next.result == 5
    assert messages.next().result == 5


def test_binding_stream_peek_skips_bind_closes_without_consuming():
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "NEW_BINDING",
        7,
        "BINDING_DELETE",
        7,
        "RESULT",
        "done",
    ]))))
    resolved = object()

    messages.bind(resolved)
    message = messages.peek()

    assert isinstance(message, ResultMessage)
    assert message.result == "done"
    assert messages.lookup_handle(7) is resolved

    assert messages.next().result == "done"
    with pytest.raises(KeyError):
        messages.lookup_handle(7)


def test_scheduler_stream_consumes_thread_switch_and_arms_checkpoint():
    callbacks = []
    calls = []

    class FakeProbe:
        def exclude(self, callback):
            return callback

        def call_at(self, *args):
            calls.append(args)

    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (1, 2, 3),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        callbacks.append,
        probe=FakeProbe(),
        initial_thread_id="main",
        current_thread_id=lambda: "main",
    )

    message = scheduler.next()

    assert len(callbacks) == 1
    assert callbacks[0].thread_id == "worker"
    assert callbacks[0].cursor_delta == (1, 2, 3)
    assert scheduler.cursor("main") == (2, 3)
    assert len(calls) == 1
    thread_id, cursor, on_hit, on_missed = calls[0]
    assert thread_id == "main"
    assert cursor == (2, 3)
    assert callable(on_hit)
    assert callable(on_missed)
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_handoffs_to_thread_switch_target():
    current = ["main"]

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0
            self.to_calls = []

        def close(self):
            self.close_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (0, 11),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        initial_thread_id="worker",
        current_thread_id=lambda: current[0],
    )

    message = scheduler.next()

    assert handoff.to_calls == ["worker"]
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_does_not_handoff_thread_switch_to_current_thread():
    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "main",
        (0, 7),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        current_thread_id=lambda: "main",
    )

    message = scheduler.next()

    assert handoff.to_calls == []
    assert scheduler.cursor("main") == (7,)
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_coalesces_adjacent_switches_before_handoff():
    current = ["worker"]

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0
            self.to_calls = []

        def close(self):
            self.close_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "main",
        (0, 1),
        "THREAD_SWITCH",
        "worker",
        (0, 2),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        initial_thread_id="worker",
        current_thread_id=lambda: current[0],
    )

    message = scheduler.next()

    assert handoff.to_calls == []
    assert scheduler.current_thread_id() == "worker"
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_does_not_handoff_terminal_switch():
    current = ["worker"]

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0
            self.to_calls = []

        def close(self):
            self.close_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "main",
        (0, 1),
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        initial_thread_id="worker",
        current_thread_id=lambda: current[0],
    )

    assert scheduler.advance_thread_schedule() is True
    assert scheduler.current_thread_id() == "main"
    assert handoff.close_calls == 1
    assert handoff.to_calls == []


def test_scheduler_stream_result_advance_skips_one_shot_target_handoff():
    current = ["main"]

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0
            self.to_calls = []

        def close(self):
            self.close_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (0, 1),
        "RESULT",
        "worker-done",
        "THREAD_SWITCH",
        "main",
        (0, 2),
        "RESULT",
        "main-done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        initial_thread_id="main",
        current_thread_id=lambda: current[0],
    )

    assert scheduler.advance_thread_schedule(
        skip_handoff_if_current_done=True,
    ) is True

    assert scheduler.current_thread_id() == "worker"
    assert handoff.to_calls == []
    assert handoff.close_calls == 0
    assert isinstance(messages.peek(), ResultMessage)
    assert messages.peek().result == "worker-done"


def test_scheduler_stream_does_not_handoff_to_dead_thread():
    current = ["main"]

    class FakeProbe:
        def call_at(self, *_args):
            return None

        def coordinates(self, thread_id):
            raise LookupError(thread_id)

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0
            self.to_calls = []

        def close(self):
            self.close_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (0, 1),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=FakeProbe(),
        handoff=handoff,
        initial_thread_id="main",
        current_thread_id=lambda: current[0],
    )

    message = scheduler.next()

    assert handoff.to_calls == []
    assert handoff.close_calls == 0
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_yields_early_target_to_previous_thread():
    current = ["main"]

    class FakeProbe:
        def call_at(self, *_args):
            return None

        def coordinates(self, _thread_id):
            return ()

    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "main",
        (0, 1),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=FakeProbe(),
        handoff=handoff,
        initial_thread_id="worker",
        current_thread_id=lambda: current[0],
    )

    message = scheduler.next()

    assert handoff.to_calls == ["worker", "main"]
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_resume_ignores_end_of_stream_probe():
    class EndedSource:
        def peek(self):
            raise RuntimeError("Could not read: 1 bytes from tracefile")

        def next(self):
            raise RuntimeError("Could not read: 1 bytes from tracefile")

    scheduler = SchedulerStream(EndedSource())

    scheduler.resume_thread()

    with pytest.raises(RuntimeError, match="Could not read"):
        scheduler.next()


def test_scheduler_stream_installs_retrace_thread_switch_callback():
    old_calls = []

    def old_switch(previous_delta, next_thread_id):
        old_calls.append((previous_delta, next_thread_id))

    class Callbacks:
        def __init__(self):
            self.thread_switch = old_switch

    class FakeProbe:
        def __init__(self):
            self.callbacks = Callbacks()

        def exclude(self, callback):
            return callback

        def call_at(self, *args):
            return None

        def disable(self, callback, *args, **kwargs):
            raise AssertionError("disable executes callbacks; exclude must wrap them")

    class FakeHandoff:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    probe = FakeProbe()
    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (0, 1),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=probe,
        handoff=handoff,
        current_thread_id=lambda: "worker",
        active=False,
    )

    scheduler.activate()

    assert probe.callbacks.thread_switch is not old_switch

    probe.callbacks.thread_switch((0, 1), "worker")

    assert old_calls == [((0, 1), "worker")]
    assert scheduler.next().result == "done"

    scheduler.deactivate()

    assert probe.callbacks.thread_switch is old_switch
    assert handoff.close_calls == 1


def test_scheduler_stream_switch_callback_consumes_one_recorded_switch():
    class Callbacks:
        def __init__(self):
            self.thread_switch = None

    class FakeProbe:
        def __init__(self):
            self.callbacks = Callbacks()

        def exclude(self, callback):
            return callback

        def call_at(self, *args):
            return None

    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    current = ["worker"]
    probe = FakeProbe()
    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "worker",
        (0, 1),
        "THREAD_SWITCH",
        "main",
        (1, 2),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=probe,
        handoff=handoff,
        initial_thread_id="main",
        current_thread_id=lambda: current[0],
        active=False,
    )

    scheduler.activate()
    probe.callbacks.thread_switch((0, 1), "worker")

    assert scheduler.current_thread_id() == "worker"
    assert handoff.to_calls == []

    assert scheduler.next().result == "done"
    assert handoff.to_calls == ["main"]


def test_scheduler_stream_switch_callback_leaves_unmatched_recorded_switch():
    call_at_calls = []

    class Callbacks:
        def __init__(self):
            self.thread_switch = None

    class FakeProbe:
        def __init__(self):
            self.callbacks = Callbacks()

        def exclude(self, callback):
            return callback

        def call_at(self, *args):
            call_at_calls.append(args)

    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    current = ["worker"]
    probe = FakeProbe()
    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_SWITCH",
        "future-worker",
        (0, 1),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=probe,
        handoff=handoff,
        initial_thread_id="main",
        current_thread_id=lambda: current[0],
        active=False,
    )

    scheduler.activate()
    probe.callbacks.thread_switch((0, 1), "worker")

    assert scheduler.current_thread_id() == "main"
    assert handoff.to_calls == []
    assert len(call_at_calls) == 1
    assert call_at_calls[0][0] == "main"
    assert call_at_calls[0][1] == (1,)

    assert scheduler.next().result == "done"
    assert scheduler.current_thread_id() == "future-worker"
    assert handoff.to_calls == ["future-worker"]


def test_scheduler_stream_does_not_set_callback_on_non_switch():
    callbacks = []
    scheduler = SchedulerStream(
        BindingStream(PeekableStream(MessageStream(read_from([
            "RESULT",
            5,
        ])))),
        callbacks.append,
    )

    assert scheduler.next().result == 5
    assert callbacks == []


def test_callback_stream_calls_callback_and_drops_completion():
    calls = []

    def callback(value):
        calls.append(value)

    def call_callback(message):
        message.fn(*message.args, **message.kwargs)

    source = SchedulerStream(BindingStream(PeekableStream(MessageStream(read_from([
        "CALLBACK",
        callback,
        ("seen",),
        {},
        "CALLBACK_RESULT",
        None,
        "RESULT",
        "done",
    ])))))
    callbacks = CallbackStream(source, call_callback)

    message = callbacks.next()

    assert calls == ["seen"]
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_callback_stream_drops_standalone_callback_completions():
    results = []
    errors = []
    error = RuntimeError("boom")
    source = SchedulerStream(BindingStream(PeekableStream(MessageStream(read_from([
        "CALLBACK_RESULT",
        "ok",
        "CALLBACK_ERROR",
        error,
        "RESULT",
        "done",
    ])))))
    callbacks = CallbackStream(
        source,
        lambda message: None,
        on_callback_result=results.append,
        on_callback_error=errors.append,
    )

    message = callbacks.next()

    assert len(results) == 1
    assert isinstance(results[0], CallbackResultMessage)
    assert results[0].result == "ok"
    assert len(errors) == 1
    assert isinstance(errors[0], CallbackErrorMessage)
    assert errors[0].error is error
    assert isinstance(message, ResultMessage)
    assert message.result == "done"
