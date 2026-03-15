import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _command_events(events, name):
    return [event for event in events if event[0] == "command" and event[1][0] == name]


def test_debug_persister_emits_low_level_events():
    events = []
    persister = stream.DebugPersister(events.append)

    with stream.writer(output=persister, flush_interval=999) as writer:
        writer("hello", 123)
        handle = writer.handle("payload")
        writer(handle)
        writer.deleter(handle)()
        writer.flush()

    object_events = [event for event in events if event[0] == "object"]
    assert ("object", "hello") in object_events
    assert ("object", 123) in object_events

    new_handle = _command_events(events, "new_handle")
    assert len(new_handle) == 1

    ref_token, payload_event = new_handle[0][1][1]
    assert isinstance(ref_token, int)
    assert payload_event == ("object", "payload")

    assert ("handle_ref", ref_token) in events
    assert ("handle_delete", ref_token) in events
    assert _command_events(events, "flush")


def test_debug_persister_handler_object_and_resume_cycle():
    class Handler:
        def __init__(self):
            self.events = []

        def handle_event(self, event):
            self.events.append(event)

    handler = Handler()
    persister = stream.DebugPersister(handler)
    writer = stream.writer(output=persister, flush_interval=999)

    try:
        writer("before-drain")
        writer.flush()

        persister.drain()
        persister.resume()

        writer("after-resume")
        writer.flush()
    finally:
        writer.disable()
        persister.close()

    object_events = [event for event in handler.events if event[0] == "object"]
    assert ("object", "before-drain") in object_events
    assert ("object", "after-resume") in object_events
    assert _command_events(handler.events, "flush")
