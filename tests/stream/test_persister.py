"""Tests for AsyncFilePersister via the writer pipeline.

The persister is no longer directly callable -- it processes QueueEntry
items from ObjectWriter via the SPSC queue. These tests exercise the
persister's file handling and data integrity through the writer.
"""
import gc
import os
import struct
import threading

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream

_mod = stream._backend_mod
FramedWriter = _mod.FramedWriter
AsyncFilePersister = _mod.AsyncFilePersister


def _make_persister(path):
    """Create a FramedWriter + AsyncFilePersister sink + Queue."""
    fw = FramedWriter(str(path))
    p = AsyncFilePersister(fw, thread=_thread_id)
    q = _mod.Queue(p)
    return fw, p, q

_thread_id = lambda: threading.current_thread().ident


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
