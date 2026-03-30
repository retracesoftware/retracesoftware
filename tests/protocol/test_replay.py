import retracesoftware.utils as utils

from retracesoftware.protocol.replay import ReplayReader


def test_async_call_swallows_callback_exceptions():
    events = []

    def callback():
        events.append("called")
        raise ValueError("boom")

    reader = ReplayReader(lambda: None, bind=utils.noop)

    reader.async_call(callback)

    assert events == ["called"]


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
