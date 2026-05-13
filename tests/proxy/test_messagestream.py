import pytest
import retracesoftware.stream as stream

from retracesoftware.protocol.messages import (
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    ResultMessage,
    StacktraceMessage,
)
from retracesoftware.proxy.messagestream import (
    BindCloseMessage,
    BindOpenMessage,
    BindingStream,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CallbackStream,
    ExpectedBindMarker,
    MessageStream,
    OnStartMessage,
    PeekableStream,
    SchedulerStream,
    ThreadResumeMessage,
    ThreadStartMessage,
    ThreadYieldMessage,
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


def test_next_message_treats_thread_switch_as_scheduler_resume():
    message = next_message(read_from(["THREAD_SWITCH", "worker"]))

    assert isinstance(message, ThreadResumeMessage)
    assert message.thread_id == "worker"


def test_next_message_reads_lifecycle_message():
    assert isinstance(next_message(read_from(["ON_START"])), OnStartMessage)


def test_next_message_reads_thread_messages():
    start = next_message(read_from(["THREAD_START", "worker"]))
    yield_message = next_message(read_from(["THREAD_YIELD", (1, 2, 3)]))
    resume = next_message(read_from(["THREAD_RESUME", "main"]))

    assert isinstance(start, ThreadStartMessage)
    assert start.thread_id == "worker"
    assert isinstance(yield_message, ThreadYieldMessage)
    assert yield_message.thread_id is None
    assert yield_message.cursor_delta == (1, 2, 3)
    assert isinstance(resume, ThreadResumeMessage)
    assert resume.thread_id == "main"


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
        "THREAD_YIELD",
        (1, 2, 3),
        "RESULT",
        10,
    ])))

    message = stream.peek()

    assert stream.peek() is message
    assert stream.next() is message
    assert isinstance(message, ThreadYieldMessage)
    assert message.thread_id is None
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


def test_scheduler_stream_sets_callback_and_drops_thread_yield():
    callbacks = []
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_YIELD",
        (1, 2, 3),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(messages, callbacks.append, initial_thread_id="worker")

    message = scheduler.next()

    assert len(callbacks) == 1
    assert callbacks[0].thread_id == "worker"
    assert callbacks[0].cursor_delta == (1, 2, 3)
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_handoffs_and_drops_thread_resume():
    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_YIELD",
        (0, 11),
        "THREAD_RESUME",
        "worker",
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        initial_thread_id="worker",
        current_thread_id=lambda: "main",
    )

    message = scheduler.next()

    assert handoff.to_calls == ["worker"]
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_does_not_handoff_resume_without_yield_cursor():
    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_RESUME",
        "worker",
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
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_installs_retrace_callbacks_and_consumes_start():
    old_calls = []

    def old_start():
        old_calls.append("start")

    def old_resume():
        old_calls.append("resume")

    class Callbacks:
        pass

    class FakeProbe:
        def __init__(self):
            self.callbacks = Callbacks()
            self.callbacks.thread_start = old_start
            self.callbacks.thread_resume = old_resume

        def exclude(self, callback):
            return callback

    class FakeHandoff:
        def __init__(self):
            self.start_calls = 0
            self.close_calls = 0

        def start(self):
            self.start_calls += 1

        def close(self):
            self.close_calls += 1

    probe = FakeProbe()
    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_START",
        "worker",
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

    assert probe.callbacks.thread_start is not old_start
    assert probe.callbacks.thread_resume is not old_resume

    probe.callbacks.thread_start()

    assert handoff.start_calls == 0
    assert old_calls == ["start"]
    probe.callbacks.thread_resume()
    assert old_calls == ["start", "resume"]
    assert scheduler.next().result == "done"

    scheduler.deactivate()

    assert probe.callbacks.thread_start is old_start
    assert probe.callbacks.thread_resume is old_resume
    assert handoff.close_calls == 1


def test_scheduler_stream_arms_retrace_checkpoint_from_thread_yield():
    class FakeProbe:
        def __init__(self):
            self.calls = []

        def exclude(self, callback):
            return callback

        def call_at(self, *args):
            self.calls.append(args)

    probe = FakeProbe()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_YIELD",
        (0, 11),
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        probe=probe,
        initial_thread_id="worker",
    )

    message = scheduler.next()

    assert isinstance(message, ResultMessage)
    assert message.result == "done"
    assert len(probe.calls) == 1
    thread_id, cursor, on_hit, on_missed = probe.calls[0]
    assert thread_id == "worker"
    assert cursor == (11,)
    assert callable(on_hit)
    assert callable(on_missed)


def test_scheduler_stream_start_thread_consumes_matching_thread_start():
    class FakeHandoff:
        def __init__(self):
            self.start_calls = 0
            self.to_calls = []

        def start(self):
            self.start_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_START",
        "worker",
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        current_thread_id=lambda: "worker",
    )

    scheduler.start_thread()
    message = scheduler.next()

    assert handoff.start_calls == 0
    assert handoff.to_calls == []
    assert isinstance(message, ResultMessage)
    assert message.result == "done"


def test_scheduler_stream_late_thread_start_claims_pending_start():
    class FakeHandoff:
        def __init__(self):
            self.start_calls = 0
            self.to_calls = []

        def start(self):
            self.start_calls += 1

        def to(self, thread_id):
            self.to_calls.append(thread_id)

    handoff = FakeHandoff()
    current = "main"
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_START",
        "worker",
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        current_thread_id=lambda: current,
    )

    assert scheduler.next().result == "done"

    current = "worker"
    scheduler.start_thread()

    assert handoff.start_calls == 0
    assert handoff.to_calls == []


def test_scheduler_stream_start_thread_does_not_park_without_start_message():
    class FakeHandoff:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        current_thread_id=lambda: "worker",
    )

    scheduler.start_thread()

    assert handoff.start_calls == 0
    assert scheduler.next().result == "done"


def test_scheduler_stream_start_thread_leaves_other_thread_start_queued():
    class FakeHandoff:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1

    handoff = FakeHandoff()
    messages = BindingStream(PeekableStream(MessageStream(read_from([
        "THREAD_START",
        "other",
        "RESULT",
        "done",
    ]))))
    scheduler = SchedulerStream(
        messages,
        handoff=handoff,
        current_thread_id=lambda: "worker",
    )

    scheduler.start_thread()

    assert handoff.start_calls == 1
    assert isinstance(messages.peek(), ThreadStartMessage)


def test_scheduler_stream_does_not_set_callback_on_non_yield():
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


def test_scheduler_stream_skips_unreplayed_thread_segment_before_binding():
    current = "main"
    raw = PeekableStream(MessageStream(read_from([
        "THREAD_RESUME",
        "helper",
        "NEW_BINDING",
        7,
        "RESULT",
        None,
        "THREAD_RESUME",
        "main",
        "RESULT",
        "done",
    ])))
    scheduler = SchedulerStream(raw, current_thread_id=lambda: current)
    messages = BindingStream(PeekableStream(scheduler))

    message = messages.next()

    assert isinstance(message, ResultMessage)
    assert message.result == "done"
    assert scheduler.current_thread_id() == "main"


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
