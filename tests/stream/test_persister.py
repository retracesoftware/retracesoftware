"""Tests for Persister via the writer pipeline.

The persister is no longer directly callable -- it processes QueueEntry
items from ObjectWriter via the SPSC queue. These tests exercise the
persister's file handling and data integrity through the writer.
"""
import gc
import pickle
import socket
import struct
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream
from retracesoftware.proxy.messagestream import MessageStream

_mod = stream._backend_mod
FramedWriter = _mod.FramedWriter
Persister = _mod.Persister
ObjectStreamReader = _mod.ObjectStreamReader
TapeReader = stream.TapeReader


def _make_persister(path):
    """Create a FramedWriter + Persister sink + Queue."""
    fw = FramedWriter(str(path))
    p = Persister(fw, serializer=pickle.dumps)
    q = _mod.Queue(p, thread=_thread_id)
    return fw, p, q


def _make_raw_native_reader(path, *, deserialize=pickle.loads, on_thread_switch=None, on_heartbeat=None):
    return ObjectStreamReader(
        path=str(path),
        deserialize=deserialize,
        stub_factory=lambda cls: cls.__new__(cls),
        create_stack_delta=lambda to_drop, frames: None,
        on_thread_switch=(lambda thread: thread) if on_thread_switch is None else on_thread_switch,
        read_timeout=1,
        verbose=False,
        on_heartbeat=on_heartbeat,
    )


def _make_tape_reader(path):
    return TapeReader(path=path, read_timeout=1, verbose=False)

def _thread_id():
    return threading.current_thread().ident


def _unframe(data: bytes) -> bytes:
    """Strip PID frame headers and return concatenated payloads."""
    out = bytearray()
    i = 0
    while i + 6 <= len(data):
        _pid, length = struct.unpack_from('<IH', data, i)
        i += 6
        out.extend(data[i:i + length])
        i += length
    return bytes(out)


# ---------------------------------------------------------------------------
# Construction / teardown
# ---------------------------------------------------------------------------

def test_construct_and_close(tmp_path):
    """Persister opens a file; close joins any threads."""
    path = tmp_path / "out.bin"
    fw, p, q = _make_persister(path)
    assert path.exists()
    q.close()
    fw.close()


def test_close_is_idempotent(tmp_path):
    """Calling close() multiple times must not crash."""
    fw, p, q = _make_persister(tmp_path / "out.bin")
    q.close()
    q.close()
    q.close()
    fw.close()


def test_dealloc_without_close(tmp_path):
    """Dropping all references should cleanly shut down."""
    path = tmp_path / "out.bin"
    fw, p, q = _make_persister(path)
    del q
    del p
    del fw
    gc.collect()
    assert path.exists()


def test_open_nonexistent_directory():
    """Opening a file in a missing directory raises IOError."""
    with pytest.raises(IOError):
        FramedWriter("/no/such/directory/file.bin")


def test_multiple_writers_can_append_same_file(tmp_path):
    """Two FramedWriters can coexist and append PID-framed payloads."""
    path = tmp_path / "shared.bin"
    fw1 = FramedWriter(str(path))
    fw2 = FramedWriter(str(path))
    try:
        fw1.write(b"first")
        fw1.flush()
        fw2.write(b"second")
        fw2.flush()
    finally:
        fw2.close()
        fw1.close()

    payload = _unframe(path.read_bytes())
    assert b"first" in payload
    assert b"second" in payload


# ---------------------------------------------------------------------------
# Writing through the writer pipeline
# ---------------------------------------------------------------------------

def test_write_single_message(tmp_path):
    """Write a single object through the writer and verify file has data."""
    path = tmp_path / "out.bin"
    with stream.writer(path, thread=_thread_id) as w:
        w("hello")
        w.flush()

    raw = path.read_bytes()
    assert len(raw) > 0
    payload = _unframe(raw)
    assert len(payload) > 0


def test_write_multiple_messages(tmp_path):
    """Multiple writes produce valid PID-framed output."""
    path = tmp_path / "out.bin"
    with stream.writer(path, thread=_thread_id) as w:
        for i in range(100):
            w(f"msg_{i}")
        w.flush()

    raw = path.read_bytes()
    payload = _unframe(raw)
    assert len(payload) > 0


def test_intern_lookup_uses_distinct_sized_tag(tmp_path):
    """Interned lookups should not reuse the generic binding lookup tag."""
    intern_path = tmp_path / "intern.bin"
    bind_path = tmp_path / "bind.bin"

    intern_value = ["interned"]
    intern_fw = FramedWriter(str(intern_path))
    intern_persister = Persister(intern_fw, serializer=pickle.dumps)
    intern_persister.intern(intern_value)
    intern_persister.write_object(intern_value)
    intern_fw.flush()
    intern_fw.close()

    bound_value = object()
    bind_fw = FramedWriter(str(bind_path))
    bind_persister = Persister(bind_fw, serializer=pickle.dumps)
    bind_persister.bind(bound_value)
    bind_persister.write_object(bound_value)
    bind_fw.flush()
    bind_fw.close()

    intern_payload = _unframe(intern_path.read_bytes())
    bind_payload = _unframe(bind_path.read_bytes())

    # Both traces end with a zero-index lookup; the control byte should differ.
    assert intern_payload[-1] != bind_payload[-1]


def test_close_drains_queue(tmp_path):
    """All queued writes must be flushed to disk before close() returns."""
    path = tmp_path / "out.bin"
    with stream.writer(path, thread=_thread_id) as w:
        for i in range(500):
            w(f"item_{i:04d}")

    raw = path.read_bytes()
    assert len(raw) > 0
    payload = _unframe(raw)
    assert len(payload) > 0


def test_open_preserves_existing_file(tmp_path):
    """Opening a writer does not truncate an existing regular file."""
    path = tmp_path / "out.bin"
    old = b"old content that should remain"
    path.write_bytes(old)

    fw, p, q = _make_persister(path)
    q.close()
    fw.close()

    assert path.read_bytes() == old


def test_append_mode(tmp_path):
    """FramedWriter always appends, so a second writer preserves existing data."""
    path = tmp_path / "out.bin"

    with stream.writer(path, thread=_thread_id) as w:
        w("first")
        w.flush()

    size_after_first = path.stat().st_size
    assert size_after_first > 0

    with stream.writer(path, thread=_thread_id) as w:
        w("second")
        w.flush()

    assert path.stat().st_size > size_after_first


def test_drain_and_resume(tmp_path):
    """Drain stops the writer thread; resume restarts it."""
    path = tmp_path / "out.bin"
    fw, p, q = _make_persister(path)
    q.drain()
    q.resume()
    q.close()
    fw.close()


def test_many_writes_stress(tmp_path):
    """Stress test: many rapid writes all get persisted."""
    path = tmp_path / "out.bin"
    with stream.writer(path, thread=_thread_id, flush_interval=999) as w:
        for i in range(5000):
            w(f"stress_{i}")
        w.flush()

    raw = path.read_bytes()
    payload = _unframe(raw)
    assert len(payload) > 0


def test_fd_getter(tmp_path):
    """The fd property returns a valid file descriptor."""
    path = tmp_path / "out.bin"
    fw = FramedWriter(str(path))
    assert fw.fd >= 0
    fw.close()
    assert fw.fd < 0


def test_path_getter(tmp_path):
    """The path property returns the file path."""
    path = tmp_path / "out.bin"
    fw = FramedWriter(str(path))
    assert fw.path == str(path)
    fw.close()


def test_raw_persister_roundtrip_with_native_reader(tmp_path):
    """Persister + raw FramedWriter roundtrips through ObjectStreamReader."""
    path = tmp_path / "raw_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    try:
        persister.write_object("hello")
        persister.write_object({"answer": 42})
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == "hello"
        assert reader() == {"answer": 42}
    finally:
        reader.close()


def test_raw_persister_pickled_float_roundtrip_with_native_reader(tmp_path):
    """Fallback-serialized floats roundtrip through pickle deserialize."""
    path = tmp_path / "raw_pickled_float_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    value = time.time()

    try:
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == value
    finally:
        reader.close()


def test_raw_persister_write_pickled_roundtrip_with_native_reader(tmp_path):
    """Pre-pickled payloads roundtrip through the native deserialize hook."""
    path = tmp_path / "raw_write_pickled_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    payload = {"pickled": [1, 2, 3]}

    try:
        persister.write_pickled(pickle.dumps(payload))
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == payload
    finally:
        reader.close()


def test_raw_persister_container_headers_roundtrip_with_native_reader(tmp_path):
    """Manual container headers reconstruct list/tuple/dict values."""
    path = tmp_path / "raw_container_headers_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    try:
        persister.start_list(3)
        persister.write_object("a")
        persister.write_object(1)
        persister.write_object({"nested": True})

        persister.start_tuple(2)
        persister.write_object("x")
        persister.write_object(2)

        persister.start_dict(2)
        persister.write_object("k1")
        persister.write_object("v1")
        persister.write_object("k2")
        persister.write_object(2)

        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == ["a", 1, {"nested": True}]
        assert reader() == ("x", 2)
        assert reader() == {"k1": "v1", "k2": 2}
    finally:
        reader.close()


def test_raw_persister_thread_switch_uses_native_callback(tmp_path):
    """Thread switch payloads are wrapped by the reader callback."""
    path = tmp_path / "raw_thread_switch_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    try:
        assert persister.write_thread_switch({"thread": 7}) is None
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(
        path,
        on_thread_switch=lambda thread: ("thread_switch", thread),
    )
    try:
        assert reader() == ("thread_switch", {"thread": 7})
    finally:
        reader.close()


def test_tape_reader_emits_thread_switch_markers(tmp_path):
    path = tmp_path / "raw_tape_reader_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    thread_a = {"thread": 1}
    thread_b = {"thread": 2}

    try:
        assert persister.write_thread_switch(thread_a) is None
        persister.write_object("a")
        persister.write_object(1)
        assert persister.write_thread_switch(thread_b) is None
        persister.write_object("b")
        persister.flush()
    finally:
        fw.close()

    reader = _make_tape_reader(path)
    try:
        switch_a = reader()
        assert isinstance(switch_a, stream.ThreadSwitch)
        assert switch_a.value == thread_a
        assert reader() == "a"
        assert reader() == 1
        switch_b = reader()
        assert isinstance(switch_b, stream.ThreadSwitch)
        assert switch_b.value == thread_b
        assert reader() == "b"
    finally:
        reader.close()


def test_tape_reader_uses_binding_stub_records_for_external_bindings(tmp_path):
    path = tmp_path / "raw_tape_reader_bindings.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    bound = object()

    try:
        persister.bind(bound)
        persister.write_object(bound)
        persister.write_delete(bound)
        persister.flush()
    finally:
        fw.close()

    reader = _make_tape_reader(path)
    try:
        create = reader()
        assert isinstance(create, stream.BindingCreate)
        assert create.index == 0

        lookup = reader()
        assert isinstance(lookup, stream.BindingLookup)
        assert lookup.index == 0

        delete = reader()
        assert isinstance(delete, stream.BindingDelete)
        assert delete.index == 0
    finally:
        reader.close()


def test_tape_reader_hydrates_interned_values(tmp_path):
    path = tmp_path / "raw_tape_reader_interns.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    value = ["interned-value"]

    try:
        persister.intern(value)
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    reader = _make_tape_reader(path)
    try:
        assert reader() == value
    finally:
        reader.close()


def test_tape_reader_emits_new_marker_for_new_patched(tmp_path):
    path = tmp_path / "raw_tape_reader_new_patched.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    patched_type = list
    value = object()

    try:
        persister.intern(patched_type)
        persister.write_new_patched(patched_type, value)
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    reader = _make_tape_reader(path)
    try:
        marker = reader()
        assert isinstance(marker, stream.NewMarker)
        assert marker.index == 1
        assert marker.cls is patched_type

        lookup = reader()
        assert lookup is marker
    finally:
        reader.close()


def test_message_stream_materializes_new_patched_markers():
    marker = stream._new_marker(7, list)
    native_reader = SimpleNamespace(stub_factory=lambda cls: cls.__new__(cls))
    source = iter(["RESULT", marker, "RESULT", marker]).__next__
    messages = MessageStream(source, native_reader=native_reader)

    materialized = messages.read_result()
    assert isinstance(materialized, list)
    assert materialized == []
    assert messages.read_result() is materialized


def test_writer_start_new_thread_uses_get_ident_and_callbacks():
    events = []

    def fake_start_new_thread(function, args, kwargs=None):
        if kwargs is None:
            function(*args)
        else:
            function(*args, **kwargs)
        return "thread-token"

    writer = stream.writer(output=stream.DebugPersister(lambda event: None))

    result = writer.start_new_thread(
        lambda value: events.append(("run", value)),
        ("payload",),
        start_new_thread=fake_start_new_thread,
        on_thread_enter=lambda ident: events.append(("enter", ident)),
        on_thread_exit=lambda ident: events.append(("exit", ident)),
        get_ident=lambda: 1234,
    )

    assert result == "thread-token"
    assert events == [("enter", 1234), ("run", "payload"), ("exit", 1234)]

    writer.__exit__()


def test_replay_start_new_thread_wrapper_supports_kwargs_and_wrapping():
    events = []

    def fake_start_new_thread(function, args, kwargs=None):
        if kwargs is None:
            function(*args)
        else:
            function(*args, **kwargs)
        return "replay-thread-token"

    def wrap_function(function):
        def wrapped(*args, **kwargs):
            events.append(("wrapped", args, kwargs))
            return function(*args, **kwargs)

        return wrapped

    wrapper = stream.replay_start_new_thread(
        fake_start_new_thread,
        on_thread_enter=lambda ident: events.append(("enter", ident)),
        on_thread_exit=lambda ident: events.append(("exit", ident)),
        wrap_function=wrap_function,
        get_ident=lambda: 5678,
    )

    result = wrapper(
        lambda *, value: events.append(("run", value)),
        (),
        {"value": "payload"},
    )

    assert result == "replay-thread-token"
    assert events == [
        ("enter", 5678),
        ("wrapped", (), {"value": "payload"}),
        ("run", "payload"),
        ("exit", 5678),
    ]


def test_with_thread_reader_attaches_current_thread_to_objects():
    thread_a = {"thread": 1}
    thread_b = {"thread": 2}
    reader = stream.WithThreadReader(iter([
        stream.ThreadSwitch(thread_a),
        "a1",
        1,
        stream.ThreadSwitch(thread_b),
        "b1",
    ]).__next__)

    assert reader() == (thread_a, "a1")
    assert reader() == (thread_a, 1)
    assert reader() == (thread_b, "b1")


def test_heartbeat_reader_strips_heartbeats_and_remembers_last():
    heartbeat_a = stream.Heartbeat("a")
    heartbeat_b = stream.Heartbeat("b")
    reader = stream.HeartbeatReader(iter([
        heartbeat_a,
        "value-1",
        heartbeat_b,
        "value-2",
    ]).__next__)

    assert reader() == "value-1"
    assert reader.last_heartbeat is heartbeat_a
    assert reader() == "value-2"
    assert reader.last_heartbeat is heartbeat_b


def test_peekable_reader_buffers_peeked_values():
    reader = stream.PeekableReader(iter([
        ("thread-a", "a1"),
        ("thread-b", "b1"),
        ("thread-a", "a2"),
    ]).__next__)

    assert reader.peek("thread-a") == ("thread-a", "a1")
    assert next(reader) == ("thread-a", "a1")
    assert reader.peek("thread-a", lambda value: value == "a2") == ("thread-a", "a2")
    assert next(reader) == ("thread-b", "b1")
    assert next(reader) == ("thread-a", "a2")


def test_peekable_reader_peeks_tape_reader_by_thread(tmp_path):
    path = tmp_path / "raw_peekable_tape_reader.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    thread_a = {"thread": 1}
    thread_b = {"thread": 2}

    try:
        assert persister.write_thread_switch(thread_a) is None
        persister.write_object("a1")
        assert persister.write_thread_switch(thread_b) is None
        persister.write_object("b1")
        assert persister.write_thread_switch(thread_a) is None
        persister.write_object("a2")
        persister.flush()
    finally:
        fw.close()

    tape = stream.WithThreadReader(_make_tape_reader(path))
    reader = stream.PeekableReader(tape)
    try:
        assert reader.peek(thread_a) == (thread_a, "a1")
        assert reader.peek(thread_a, lambda value: value == "a2") == (thread_a, "a2")
        assert next(reader) == (thread_a, "a1")
        assert reader.peek(thread_b) == (thread_b, "b1")
        assert next(reader) == (thread_b, "b1")
        assert next(reader) == (thread_a, "a2")
    finally:
        reader.close()


def test_demux_reader_dispatches_values_by_thread():
    source = stream.PeekableReader(iter([
        ("thread-a", "a1"),
        ("thread-b", "b1"),
        ("thread-a", "a2"),
    ]).__next__)
    reader = stream.DemuxReader(source)

    assert reader.pending("thread-a") == "a1"
    with pytest.raises(KeyError):
        reader.pending("thread-b")
    assert reader.peek("thread-a") == "a1"
    assert reader.next("thread-a") == "a1"
    assert reader.peek("thread-a", lambda value: value == "a2") == "a2"
    assert reader.next("thread-b") == "b1"
    assert reader("thread-a") == "a2"


def test_demux_reader_dispatches_tape_reader_by_thread(tmp_path):
    path = tmp_path / "raw_demux_tape_reader.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    thread_a = {"thread": 1}
    thread_b = {"thread": 2}

    try:
        assert persister.write_thread_switch(thread_a) is None
        persister.write_object("a1")
        assert persister.write_thread_switch(thread_b) is None
        persister.write_object("b1")
        assert persister.write_thread_switch(thread_a) is None
        persister.write_object("a2")
        persister.flush()
    finally:
        fw.close()

    source = stream.PeekableReader(stream.WithThreadReader(_make_tape_reader(path)))
    reader = stream.DemuxReader(source)
    try:
        assert reader.pending(thread_a) == "a1"
        with pytest.raises(KeyError):
            reader.pending(thread_b)
        assert reader.peek(thread_a) == "a1"
        assert reader.next(thread_a) == "a1"
        assert reader.peek(thread_b) == "b1"
        assert reader.next(thread_b) == "b1"
        assert reader.next(thread_a) == "a2"
    finally:
        reader.close()


def test_resolving_reader_resolves_bound_lookups_and_skips_deletes(tmp_path):
    path = tmp_path / "raw_resolving_reader.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    bound = object()

    try:
        persister.bind(bound)
        persister.write_object(bound)
        persister.write_delete(bound)
        persister.write_object("after")
        persister.flush()
    finally:
        fw.close()

    source = stream.DemuxReader(stream.PeekableReader(stream.WithThreadReader(_make_tape_reader(path))))
    reader = stream.ResolvingReader(source)
    resolved = object()
    try:
        reader.bind(resolved)
        assert reader.next(None) is resolved
        assert reader.next(None) == "after"
    finally:
        reader.close()


def test_resolving_reader_peek_resolves_values_using_binding_table(tmp_path):
    path = tmp_path / "raw_resolving_reader_peek.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    bound = object()

    try:
        persister.bind(bound)
        persister.write_object(bound)
        persister.write_delete(bound)
        persister.write_object("after")
        persister.flush()
    finally:
        fw.close()

    reader = stream.ObjectReader(thread_id=lambda: None, source=_make_tape_reader(path))
    resolved = object()
    try:
        reader.bind(resolved)
        assert reader.peek(None) is resolved
        assert reader.next(None) is resolved
        assert reader.peek(None) == "after"
        assert reader.next(None) == "after"
    finally:
        reader.close()


def test_resolving_reader_resolves_new_marker_class_lookup(tmp_path):
    path = tmp_path / "raw_resolving_reader_new_marker.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    patched_type = list
    patched_value = object()

    try:
        persister.bind(patched_type)
        persister.write_new_patched(patched_type, patched_value)
        persister.write_object(patched_value)
        persister.flush()
    finally:
        fw.close()

    reader = stream.ObjectReader(thread_id=lambda: None, source=_make_tape_reader(path))
    resolved_type = dict
    try:
        reader.bind(resolved_type)
        marker = reader.next(None)
        assert isinstance(marker, stream.NewMarker)
        assert marker.index == 0
        assert marker.cls is resolved_type
        assert reader.next(None) is marker
    finally:
        reader.close()


def test_raw_persister_non_bytes_serializer_emits_serialize_error(tmp_path):
    """Non-bytes serializer results are emitted under SERIALIZE_ERROR."""
    path = tmp_path / "raw_serialize_error_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=lambda obj: "socket-error")

    sock = socket.socket()
    try:
        persister.write_object(sock)
        persister.flush()
    finally:
        sock.close()
        fw.close()

    raw = path.read_bytes()
    assert raw[0] == 0xEE

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == "socket-error"
    finally:
        reader.close()


def test_raw_persister_serializer_exception_raises_and_writes_nothing(tmp_path):
    """Serializer exceptions return NULL to Python and emit no bytes."""
    path = tmp_path / "raw_serialize_exception.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    sock = socket.socket()
    try:
        with pytest.raises(TypeError):
            persister.write_object(sock)
        persister.flush()
    finally:
        sock.close()
        fw.close()

    assert path.read_bytes() == b""


def test_raw_persister_intern_roundtrip_with_native_reader(tmp_path):
    """Interned values stay readable via the native binding lookup path."""
    path = tmp_path / "raw_intern_roundtrip.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    value = "interned-value"

    try:
        persister.intern(value)
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == value
    finally:
        reader.close()


def test_raw_persister_interns_builtin_bools_roundtrip(tmp_path):
    """Builtin bool intern records roundtrip through the normal serializer path."""
    path = tmp_path / "raw_bool_bootstrap.bin"
    fw = FramedWriter(str(path), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)

    try:
        assert persister.intern(True) is None
        assert persister.intern(False) is None
        assert persister.write_object(True) is None
        assert persister.write_object(False) is None
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() is True
        assert reader() is False
    finally:
        reader.close()


def test_write_delete_ignores_interned_values(tmp_path):
    """Interned values live for the writer lifetime and do not emit deletes."""
    without_delete = tmp_path / "intern_without_delete.bin"
    with_delete = tmp_path / "intern_with_delete.bin"
    value = ["interned-value"]

    fw = FramedWriter(str(without_delete), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    try:
        persister.intern(value)
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    fw = FramedWriter(str(with_delete), raw=True)
    persister = Persister(fw, serializer=pickle.dumps)
    try:
        persister.intern(value)
        persister.write_delete(value)
        persister.write_object(value)
        persister.flush()
    finally:
        fw.close()

    assert with_delete.read_bytes() == without_delete.read_bytes()
