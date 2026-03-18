import gc
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
        del handle
        gc.collect()
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
    assert ("handle_delete", 0) in events
    assert _command_events(events, "flush")


def test_debug_persister_resume_cycle():
    class Handler:
        def __init__(self):
            self.events = []

        def handle_event(self, event):
            self.events.append(event)

    handler = Handler()
    persister = stream.DebugPersister(handler)
    writer = stream.writer(output=persister, flush_interval=999)
    queue = writer.queue

    try:
        writer("before-drain")
        writer.flush()

        queue.drain()
        queue.resume()

        writer("after-resume")
        writer.flush()
    finally:
        writer.disable()
        queue.close()

    object_events = [event for event in handler.events if event[0] == "object"]
    assert ("object", "before-drain") in object_events
    assert ("object", "after-resume") in object_events
    assert _command_events(handler.events, "flush")


def test_writer_wraps_plain_python_persister():
    class RecordingPersister:
        def __init__(self):
            self.events = []

        def consume_object(self, obj):
            self.events.append(("object", obj))

        def consume_handle_ref(self, index):
            self.events.append(("handle_ref", index))

        def consume_handle_delete(self, delta):
            self.events.append(("handle_delete", delta))

        def consume_ref(self, index):
            self.events.append(("bound_ref", index))

        def consume_intern(self, obj):
            self.events.append(("intern", obj))

        def consume_bound_ref_delete(self, index):
            self.events.append(("bound_ref_delete", index))

        def consume_flush(self):
            self.events.append(("flush",))

        def consume_shutdown(self):
            self.events.append(("shutdown",))

        def consume_list(self, length):
            self.events.append(("list", length))

        def consume_tuple(self, length):
            self.events.append(("tuple", length))

        def consume_dict(self, length):
            self.events.append(("dict", length))

        def consume_heartbeat(self):
            self.events.append(("heartbeat",))

        def consume_new_ext_wrapped(self, typ):
            self.events.append(("new_ext_wrapped", typ))

        def consume_delete(self, ref_id):
            self.events.append(("delete", ref_id))

        def consume_thread_switch(self, thread_handle):
            self.events.append(("thread_switch", thread_handle))

        def consume_pickled(self, obj):
            self.events.append(("pickled", obj))

        def consume_new_handle(self, index, obj):
            self.events.append(("new_handle", index, obj))

        def consume_new_patched(self, obj, typ):
            self.events.append(("new_patched", type(obj), typ))

        def consume_bind(self, index):
            self.events.append(("bind", index))
    
        def consume_serialize_error(self):
            self.events.append(("serialize_error",))

    handler = RecordingPersister()

    with stream.writer(output=handler, flush_interval=999) as writer:
        writer("wrapped-output")
        writer.flush()

    assert ("object", "wrapped-output") in handler.events
    assert ("flush",) in handler.events


def test_debug_persister_emits_bound_ref_for_bound_patched_object():
    events = []
    persister = stream.DebugPersister(events.append)

    class Patched:
        __retrace_system__ = object()

    with stream.writer(output=persister, flush_interval=999) as writer:
        writer.bind(Patched)
        obj = Patched()
        writer.bind(obj)
        writer(obj)
        writer.flush()

    assert ("command", ("bind", (0,))) in events
    assert ("command", ("bind", (1,))) in events
    assert ("bound_ref", 1) in events


def test_debug_persister_emits_intern_then_ref():
    events = []
    persister = stream.DebugPersister(events.append)

    payload = "interned payload"

    with stream.writer(output=persister, flush_interval=999) as writer:
        writer.intern(payload)
        writer(payload)
        writer.flush()

    assert ("command", ("intern", (0, ("object", payload)))) in events
    assert ("bound_ref", 0) in events


def test_new_patched_uses_bind_lifecycle_tracking():
    events = []
    persister = stream.DebugPersister(events.append)

    class Patched:
        __retrace_system__ = object()

    with stream.writer(output=persister, flush_interval=999) as writer:
        obj = Patched()
        assert writer.new_patched(obj) is None
        writer.flush()
        del obj
        for _ in range(3):
            gc.collect()
            writer.flush()

    assert _command_events(events, "new_patched")
    assert any(event[0] == "bound_ref_delete" and isinstance(event[1], int) for event in events)
    assert any(event[0] == "command" and event[1][0] == "delete" for event in events)


def test_debug_persister_emits_new_ext_wrapped_for_external_result():
    import retracesoftware.utils as utils

    events = []
    persister = stream.DebugPersister(events.append)

    class Payload:
        pass

    class Proxy(utils.Wrapped):
        pass

    wrapped = utils.create_wrapped(Proxy, Payload())

    with stream.writer(output=persister, flush_interval=999) as writer:
        writer(wrapped)
        writer.flush()

    assert _command_events(events, "new_ext_wrapped")
