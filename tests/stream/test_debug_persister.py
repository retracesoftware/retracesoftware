import gc
import time
import weakref
import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _command_events(events, name):
    return [event for event in events if event[0] == "command" and event[1][0] == name]


def _intern_payloads(events):
    return [
        event[1][1][1]
        for event in _command_events(events, "intern")
        if len(event[1][1]) >= 2
    ]


def _disable_heartbeat(monkeypatch):
    monkeypatch.setattr(stream, "call_periodically", lambda interval, func: None)


def test_debug_persister_emits_low_level_events(monkeypatch):
    _disable_heartbeat(monkeypatch)
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
    assert ("object", "hello") in object_events or ("object", "hello") in _intern_payloads(events)
    assert ("object", 123) in object_events

    assert any(
        event[0] == "object" and "payload" in repr(event[1])
        for event in events
    )
    assert _command_events(events, "flush")


def test_debug_persister_resume_cycle(monkeypatch):
    _disable_heartbeat(monkeypatch)
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


def test_writer_wraps_plain_python_persister(monkeypatch):
    _disable_heartbeat(monkeypatch)
    class RecordingPersister:
        def __init__(self):
            self.events = []

        def write_object(self, obj):
            self.events.append(("object", obj))

        def write_handle_ref(self, index):
            self.events.append(("handle_ref", index))

        def write_handle_delete(self, delta):
            self.events.append(("handle_delete", delta))

        def intern(self, obj, ref):
            self.events.append(("intern", obj, ref))

        def write_bound_ref_delete(self, index):
            self.events.append(("bound_ref_delete", index))

        def flush(self):
            self.events.append(("flush",))

        def shutdown(self):
            self.events.append(("shutdown",))

        def start_collection(self, typ, length):
            self.events.append((typ.__name__, length))

        def write_heartbeat(self):
            self.events.append(("heartbeat",))

        def write_delete(self, ref):
            self.events.append(("delete", ref))

        def write_thread_switch(self, thread_handle):
            self.events.append(("thread_switch", thread_handle))

        def write_pickled(self, obj):
            self.events.append(("pickled", obj))

        def write_new_handle(self, index, obj):
            self.events.append(("new_handle", index, obj))

        def write_new_patched(self, typ, ref):
            self.events.append(("new_patched", typ, ref))

        def bind(self, ref):
            self.events.append(("bind", ref))
    
        def write_serialize_error(self):
            self.events.append(("serialize_error",))

    handler = RecordingPersister()

    with stream.writer(output=handler, flush_interval=999) as writer:
        writer("wrapped-output")
        writer.flush()

    assert ("object", "wrapped-output") in handler.events
    assert ("flush",) in handler.events


def test_consumer_error_callback_shuts_queue_down():
    errors = []

    class FailingPersister:
        def __init__(self):
            self.events = []

        def write_object(self, obj):
            self.events.append(("object", obj))
            raise RuntimeError("boom")

        def shutdown(self):
            self.events.append(("shutdown",))

    handler = FailingPersister()
    queue = stream._backend_mod.Queue(
        handler,
        on_target_error=errors.append,
    )
    writer = stream._backend_mod.ObjectWriter(queue, object)

    try:
        writer("before-error")

        deadline = time.time() + 2
        while not errors and time.time() < deadline:
            time.sleep(0.01)

        assert errors
        assert "boom" in errors[0]
        assert handler.events.count(("shutdown",)) == 1

        writer("after-error")
        assert handler.events == [("object", "before-error"), ("shutdown",)]
    finally:
        queue.close()


def test_raw_queue_passes_collection_lengths_as_python_ints():
    class RecordingPersister:
        def __init__(self):
            self.events = []

        def start_collection(self, typ, length):
            self.events.append((typ, length))

        def write_object(self, obj):
            self.events.append(("object", obj))

    handler = RecordingPersister()
    queue = stream._backend_mod.Queue(handler)
    writer = stream._backend_mod.ObjectWriter(queue, object)

    try:
        writer([1, 2])
        writer((3, 4, 5))
        writer({1: 2, 3: 4})
        writer.flush()

        deadline = time.time() + 2
        while len([event for event in handler.events if event[0] in {list, tuple, dict}]) < 3 and time.time() < deadline:
            time.sleep(0.01)

        assert (list, 2) in handler.events
        assert (tuple, 3) in handler.events
        assert (dict, 2) in handler.events
    finally:
        queue.close()


def test_debug_persister_emits_bound_ref_for_bound_patched_object(monkeypatch):
    _disable_heartbeat(monkeypatch)
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


def test_debug_persister_emits_intern_then_ref(monkeypatch):
    _disable_heartbeat(monkeypatch)
    events = []
    persister = stream.DebugPersister(events.append)

    payload = "interned payload"

    with stream.writer(output=persister, flush_interval=999) as writer:
        writer.intern(payload)
        writer(payload)
        writer.flush()

    assert ("command", ("intern", (0, ("object", payload)))) in events
    assert ("bound_ref", 0) in events


def test_async_new_patched_uses_bind_lifecycle_tracking(monkeypatch):
    _disable_heartbeat(monkeypatch)
    events = []
    persister = stream.DebugPersister(events.append)

    class Patched:
        __retrace_system__ = object()

    with stream.writer(output=persister, flush_interval=999) as writer:
        obj = Patched()
        assert writer.handle("ASYNC_NEW_PATCHED")(obj) is None
        writer.flush()
        del obj
        for _ in range(3):
            gc.collect()
            writer.flush()

    assert any(
        event[0] == "command"
        and event[1][0] == "intern"
        and event[1][1][1] == ("object", "ASYNC_NEW_PATCHED")
        for event in events
    )
    assert any(event[0] == "object" and type(event[1]).__name__ == "Patched" for event in events)


def test_async_new_patched_handles_nonweakrefable_instance_tokens(monkeypatch):
    _disable_heartbeat(monkeypatch)
    events = []
    persister = stream.DebugPersister(events.append)

    class Patched:
        __retrace_system__ = object()
        __slots__ = ()

    obj = Patched()
    with pytest.raises(TypeError):
        weakref.ref(obj)

    with stream.writer(output=persister, flush_interval=999) as writer:
        assert writer.handle("ASYNC_NEW_PATCHED")(obj) is None
        writer.flush()

    assert any(
        event[0] == "command"
        and event[1][0] == "intern"
        and event[1][1][1] == ("object", "ASYNC_NEW_PATCHED")
        for event in events
    )
    assert any(event[0] == "object" and type(event[1]).__name__ == "Patched" for event in events)


def test_debug_persister_emits_new_ext_wrapped_for_external_result(monkeypatch):
    _disable_heartbeat(monkeypatch)
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

    assert any(
        event == ("bound_ref", 0) or (event[0] == "object" and isinstance(event[1], Proxy))
        for event in events
    )
