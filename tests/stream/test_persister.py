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

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream

_mod = stream._backend_mod
FramedWriter = _mod.FramedWriter
Persister = _mod.Persister
ObjectStreamReader = _mod.ObjectStreamReader


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
    assert raw[0] == 0xFD

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
        persister.write_ref(value)
        persister.flush()
    finally:
        fw.close()

    reader = _make_raw_native_reader(path)
    try:
        assert reader() == value
    finally:
        reader.close()
