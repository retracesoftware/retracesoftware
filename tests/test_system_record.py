"""Focused system-recording tests.

These tests intentionally sit above the low-level queue/persister unit tests.
The goal here is to document and verify how three layers fit together:

1. ``proxy.system.System`` allocation/binding hooks
2. ``stream.ObjectWriter`` queue emission behavior
3. the queue protocol surface seen by a native or Python-backed queue

The file uses a tiny Python ``RecordingQueue`` so the assertions can be made
against the queue calls directly without having to decode a full persisted
stream.  That keeps each test explicit about which signal it is checking:
``push_bind``, ``push_new_patched``, passthrough/no-op behavior, and so on.

These tests are intentionally narrow:
- they are not trying to validate the full replay pipeline
- they are not trying to validate low-level queue encoding
- they are trying to validate which recording hook fires for a given system
  state and whether ``ObjectWriter`` forwards that hook to the queue surface

When one of these tests fails, the failure should answer:
"which recording hook was used?" rather than
"did the whole stream stack work end-to-end?"
"""

import pytest

pytest.importorskip("retracesoftware.stream")

import retracesoftware.stream as stream
import retracesoftware.proxy.system as proxy_system
from retracesoftware.install import stream_writer
from retracesoftware.proxy.messagestream import MessageStream


class RecordingQueue:
    """Minimal Python queue protocol implementation for tests.

    ``ObjectWriter`` now supports a Python-object slow path for queues.  This
    test double records every ``push_*`` call and always returns ``True`` so
    the writer stays enabled.  That lets the tests assert on the exact queue
    method chosen by the system/writer wiring.
    """

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("push_"):
            raise AttributeError(name)

        def push(*args):
            self.calls.append((name, args))
            return True

        return push

    def clear(self):
        self.calls.clear()

    def named(self, name):
        return [args for method, args in self.calls if method == name]


class SystemRecordHarness:
    """Small writer-like object for ``System.record_context`` tests.

    ``System.record_context`` expects a writer with methods such as ``bind``,
    ``async_new_patched``, ``sync``, and ``write_result``.  The real high-level
    stream writer carries much more behavior than these tests need, so this
    harness delegates the binding/allocation operations to the native
    ``stream.ObjectWriter`` while keeping the other callbacks as simple
    in-memory bookkeeping.
    """

    def __init__(self, queue):
        self.queue = queue
        self.object_writer = stream.ObjectWriter(queue, lambda obj: obj)
        self.type_serializer = {}
        self.calls = []

    def bind(self, obj):
        self.calls.append(("bind", obj))
        return self.object_writer.bind(obj)

    def intern(self, obj):
        self.calls.append(("intern", obj))
        return self.object_writer.intern(obj)

    def async_new_patched(self, obj):
        self.calls.append(("async_new_patched", obj))
        tag = "ASYNC_NEW_PATCHED"
        self.object_writer.intern(tag)
        if self.object_writer._native is not None:
            self.object_writer._native(tag, obj)
        else:
            self.queue.push_ref(self.object_writer._bind_token(tag))
            self.queue.push_obj(obj)
        return None

    def sync(self):
        self.calls.append(("sync",))

    def async_call(self, *args, **kwargs):
        self.calls.append(("async_call", args, kwargs))

    def write_result(self, value):
        self.calls.append(("write_result", value))

    def write_error(self, exc_type, exc_value, exc_tb):
        self.calls.append(("write_error", exc_type, exc_value, exc_tb))

    def checkpoint(self, value):
        self.calls.append(("checkpoint", value))

    def stacktrace(self):
        self.calls.append(("stacktrace",))


class QueueBackedDebugHarness(SystemRecordHarness):
    """System-record harness using the real native Queue worker path."""

    def __init__(self):
        self.events = []
        self.persister = stream.DebugPersister(self.events.append)
        self.native_queue = stream._backend_mod.Queue(self.persister)
        super().__init__(self.native_queue)

    def close(self):
        self.native_queue.drain()
        self.native_queue.close()


def test_objectwriter_bind_uses_python_queue_fallback():
    """``ObjectWriter.bind`` should use the Python queue slow path.

    This is the lowest-level test in the file.  It does not involve
    ``System`` at all.  The purpose is to document the contract introduced by
    the queue fast-path/slow-path refactor:

    - if the queue is not the exact native ``Queue`` type
    - ``ObjectWriter`` should call ``push_bind`` on the Python object
    - opaque refs should cross that boundary as Python integers
    """

    queue = RecordingQueue()
    writer = stream.ObjectWriter(queue, lambda obj: obj)

    obj = object()
    assert writer.bind(obj) is None

    bind_calls = queue.named("push_bind")
    assert len(bind_calls) == 1
    assert len(bind_calls[0]) == 1
    assert isinstance(bind_calls[0][0], int)


def test_system_record_binds_allocations_in_sandbox():
    """Allocations in the normal sandbox phase should emit ``bind``.

    ``System._on_alloc`` distinguishes between two retrace phases:

    - internal/sandbox phase: the object already exists in-process, so it only
      needs a stream identity and should go through ``writer.bind``
    - external-call phase: the object must be materialized by type on replay,
      so it should go through ``writer.new_patched``

    This test documents the first case.
    """

    system = proxy_system.System()
    queue = RecordingQueue()
    writer = SystemRecordHarness(queue)

    class Patched:
        pass

    system.patch_type(Patched)

    with system.record_context(writer):
        obj = Patched()
        assert system.is_bound(obj)

    bind_calls = queue.named("push_bind")
    assert len(bind_calls) == 1
    assert isinstance(bind_calls[0][0], int)
    assert not queue.named("push_new_patched")


def test_system_record_uses_async_new_patched_during_external_phase_allocation():
    """Allocations during an external call should emit ``new_patched`` then ``bind``.

    After entering ``record_context``, calling a patched base-type method moves
    execution into the temporary external-call phase.  If a new patched object
    is allocated there, replay needs a stronger signal than ``bind`` so it can
    reconstruct the object by type.  Once the object exists, it should still
    enter the normal binding lifecycle, so the queue-visible effect is
    ``NEW_PATCHED`` protocol writes followed by ``push_bind``.
    """

    system = proxy_system.System()
    queue = RecordingQueue()
    writer = SystemRecordHarness(queue)

    class Patched:
        def make_peer(self):
            return type(self)()

    system.patch_type(Patched)

    with system.record_context(writer):
        root = Patched()
        queue.clear()
        peer = root.make_peer()
        assert peer is not None

    assert len(queue.named("push_intern")) == 1
    assert len(queue.named("push_ref")) == 1
    assert len(queue.named("push_obj")) == 1
    assert len(queue.named("push_bind")) == 1


def test_system_record_passthroughs_unbound_instances():
    """Objects created outside retrace should stay on the passthrough path.

    ``System`` leaves patched objects created outside any active
    record/replay context unbound. Calls on those objects should bypass
    the recording pipeline even when a later ``record_context`` is active.

    This test documents that invariant by asserting both:
    - the object is reported as not bound
    - invoking one of its patched methods produces no queue traffic
    """

    system = proxy_system.System()
    queue = RecordingQueue()
    writer = SystemRecordHarness(queue)

    class Patched:
        def ping(self):
            return "pong"

    system.patch_type(Patched)

    obj = Patched()
    assert not system.is_bound(obj)

    with system.record_context(writer):
        queue.clear()
        assert obj.ping() == "pong"

    assert queue.calls == []


def test_system_record_bind_reaches_python_persister_via_native_queue():
    """Sandbox binds should survive native Queue -> Python persister dispatch."""

    system = proxy_system.System()
    writer = QueueBackedDebugHarness()

    class Patched:
        pass

    system.patch_type(Patched)

    try:
        with system.record_context(writer):
            obj = Patched()
            assert system.is_bound(obj)
    finally:
        writer.close()

    assert ("command", ("bind", (0,))) in writer.events


def test_system_record_async_new_patched_reaches_python_persister_via_native_queue():
    """External-phase allocations should survive native Queue -> Python persister dispatch."""

    system = proxy_system.System()
    writer = QueueBackedDebugHarness()

    class Patched:
        def make_peer(self):
            return type(self)()

    system.patch_type(Patched)

    try:
        with system.record_context(writer):
            root = Patched()
            writer.events.clear()
            peer = root.make_peer()
            assert peer is not None
    finally:
        writer.close()

    assert any(
        event[0] == "command"
        and event[1][0] == "intern"
        and event[1][1][1] == ("object", "ASYNC_NEW_PATCHED")
        for event in writer.events
    )
    assert any(event[0] == "bound_ref" for event in writer.events)
    assert any(event[0] == "object" and event[1].__name__ == "Patched" for event in writer.events)


def test_system_record_memoryview_result_roundtrips_through_unframed_binary_replay(tmp_path):
    """Readonly memoryview results should survive record + replay.

    This is a focused regression for proxied external results. The patched
    method returns a readonly ``memoryview``; replay should be able to read
    back the recorded result from an unframed file-backed trace.
    """

    system = proxy_system.System()
    system.immutable_types.update({memoryview, int, float, str, bytes, bool, type, type(None)})
    path = tmp_path / "memoryview.bin"

    class Patched:
        def payload(self):
            return memoryview(b"payload")

    system.patch_type(Patched)

    with stream.writer(path, flush_interval=999, format="unframed_binary") as raw_writer:
        writer = stream_writer(raw_writer)
        with system.record_context(writer):
            obj = Patched()
            result = obj.payload()
            assert isinstance(result, memoryview)
            assert result.tobytes() == b"payload"

    assert path.stat().st_size > 0

    with stream.reader(path, read_timeout=1, verbose=False) as raw_reader:
        per_thread_source = stream.per_thread(
            source=raw_reader,
            thread=lambda: (),
            timeout=1,
        )
        msg_stream = MessageStream(per_thread_source, native_reader=raw_reader)

        with system.replay_context(msg_stream):
            obj = Patched()
            replayed = obj.payload()
            assert bytes(replayed) == b"payload"
