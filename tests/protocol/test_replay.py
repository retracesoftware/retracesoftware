import pytest
import retracesoftware.utils as utils

from retracesoftware.install import ReplayDivergence
from retracesoftware.protocol import CALL, StacktraceMessage
from retracesoftware.protocol.normalize import normalize as normalize_checkpoint_value
from retracesoftware.protocol.replay import ReplayReader, StacktraceFactory, next_message
from retracesoftware.testing.memorytape import MemoryWriter


class FakeStackFactory:
    def __init__(self, *deltas):
        self._deltas = list(deltas)
        self.exclude = set()

    def delta(self):
        return self._deltas.pop(0)


def test_async_call_swallows_callback_exceptions():
    events = []

    def callback():
        events.append("called")
        raise ValueError("boom")

    reader = ReplayReader(lambda: None, bind=utils.noop)

    reader.async_call(callback)

    assert events == ["called"]


def test_async_call_reraises_replay_divergence():
    def callback():
        raise ReplayDivergence("boom")

    reader = ReplayReader(lambda: None, bind=utils.noop)

    with pytest.raises(ReplayDivergence, match="boom"):
        reader.async_call(callback)


def test_read_result_continues_after_raising_async_call():
    events = []

    def callback():
        events.append("called")
        raise ValueError("boom")

    tape = iter([
        "ASYNC_CALL",
        callback,
        (),
        {},
        "RESULT",
        99,
    ])

    reader = ReplayReader(lambda: next(tape), bind=utils.noop)

    assert reader.read_result() == 99
    assert events == ["called"]


def test_write_call_accepts_call_marker():
    tape = iter([CALL])

    reader = ReplayReader(lambda: next(tape), bind=utils.noop)

    reader.write_call()


def test_write_call_validates_matching_stacktrace():
    delta = (
        0,
        (
            (
                ("/tmp/app.py", 10),
                ("/tmp/library.py", 20),
            ),
        ),
    )
    tape = iter(["STACKTRACE", delta[0], delta[1], CALL])

    reader = ReplayReader(
        lambda: next(tape),
        bind=utils.noop,
        stacktrace_factory=FakeStackFactory(delta),
    )

    reader.write_call()


def test_next_message_materializes_full_stacktrace_message():
    delta = (
        0,
        (
            (
                ("/tmp/app.py", 10),
                ("/tmp/library.py", 20),
            ),
        ),
    )

    msg = next_message(
        iter(["STACKTRACE", delta[0], delta[1]]).__next__,
        stacktrace_factory=StacktraceFactory().materialize,
    )

    assert isinstance(msg, StacktraceMessage)
    assert msg.stacktrace == delta[1][0]


def test_write_call_raises_on_stacktrace_divergence():
    recorded_delta = (
        0,
        (
            (
                ("/tmp/app.py", 10),
                ("/tmp/library.py", 20),
            ),
        ),
    )
    replay_delta = (
        0,
        (
            (
                ("/tmp/app.py", 10),
                ("/tmp/other.py", 99),
            ),
        ),
    )
    tape = iter([
        StacktraceFactory().materialize(*recorded_delta),
        CALL,
    ])

    reader = ReplayReader(
        lambda: next(tape),
        bind=utils.noop,
        stacktrace_factory=FakeStackFactory(replay_delta),
    )

    with pytest.raises(ReplayDivergence, match="stacktrace divergence"):
        reader.write_call()


def test_checkpoint_normalizes_values_symmetrically():
    class Thing:
        pass

    def callback():
        return None

    delta = (0, ())
    value = {
        "fn": callback,
        "obj": Thing(),
        "nested": [Thing(), callback],
        "flag": True,
    }

    writer = MemoryWriter(stackfactory=FakeStackFactory(delta))
    writer.checkpoint(value)

    assert isinstance(writer.tape[0], StacktraceMessage)
    assert writer.tape[1] == "CHECKPOINT"
    assert writer.tape[2] == normalize_checkpoint_value(value)

    tape = iter([
        StacktraceFactory().materialize(0, ()),
        "CHECKPOINT",
        normalize_checkpoint_value(value),
    ])
    reader = ReplayReader(
        lambda: next(tape),
        bind=utils.noop,
        stacktrace_factory=FakeStackFactory(delta),
    )
    reader.checkpoint(value)


def test_read_result_skips_nested_sync_call_frames():
    tape = iter([
        CALL,
        "CHECKPOINT",
        True,
        CALL,
        "CHECKPOINT",
        {"function": "recv", "args": (), "kwargs": {}},
        "RESULT",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n",
        "CHECKPOINT",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n",
        "RESULT",
        b"GET /health HTTP/1.1\r\n",
    ])

    reader = ReplayReader(lambda: next(tape), bind=utils.noop)

    assert reader.read_result() == b"GET /health HTTP/1.1\r\n"
