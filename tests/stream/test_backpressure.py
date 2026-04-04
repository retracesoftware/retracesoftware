"""Tests for oversized messages and message boundary framing."""
import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _thread_id() -> str:
    return "main-thread"


def _read_all(reader):
    """Read all values from a reader until EOF."""
    values = []
    try:
        while True:
            values.append(reader())
    except Exception:
        pass
    return values


def _read_values(reader):
    """Read all non-control values from a reader until EOF."""
    return [v for v in _read_all(reader) if not isinstance(v, stream.Control)]


# ---------------------------------------------------------------------------
# Oversized messages (> 64KB)
# ---------------------------------------------------------------------------

def test_oversized_bytes_roundtrip(tmp_path):
    """A single bytes object larger than the 64KB buffer round-trips."""
    path = tmp_path / "trace.bin"
    big = b"X" * (65536 * 2)

    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        w(big)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        val = _read_values(r)
        assert val == [big]


def test_oversized_string_roundtrip(tmp_path):
    """A single large string that exceeds the buffer size round-trips."""
    path = tmp_path / "trace.bin"
    big_str = "A" * (65536 * 3)

    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        w(big_str)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        val = _read_values(r)
        assert val == [big_str]


def test_oversized_list_roundtrip(tmp_path):
    """A list with enough entries to exceed the buffer size round-trips."""
    path = tmp_path / "trace.bin"
    big_list = list(range(20000))

    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        w(big_list)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        val = _read_values(r)
        assert val == [big_list]


def test_oversized_then_normal(tmp_path):
    """An oversized message followed by normal messages all round-trip."""
    path = tmp_path / "trace.bin"
    big = b"Z" * (65536 * 2)

    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        w(big)
        w("small_1", 42)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        val = _read_values(r)
        assert val == [big, "small_1", 42]


# ---------------------------------------------------------------------------
# Message boundary framing (messages straddling buffer boundary)
# ---------------------------------------------------------------------------

def test_messages_across_buffer_boundary(tmp_path):
    """Many messages that collectively exceed the 64KB buffer all survive."""
    path = tmp_path / "trace.bin"
    count = 3000

    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as w:
        for i in range(count):
            w(f"boundary_{i:05d}")
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        vals = _read_values(r)

    assert len(vals) == count
    for i, v in enumerate(vals):
        assert v == f"boundary_{i:05d}", f"Mismatch at index {i}: {v!r}"


def test_mixed_sizes_across_boundary(tmp_path):
    """Messages of varying sizes that straddle the buffer boundary."""
    path = tmp_path / "trace.bin"
    expected = []

    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as w:
        for i in range(500):
            val = "X" * ((i % 200) + 1)
            expected.append(val)
            w(val)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        vals = _read_values(r)

    assert vals == expected


# ---------------------------------------------------------------------------
# Wait mode (default — no dropping, all messages preserved)
# ---------------------------------------------------------------------------

def test_wait_mode_no_data_loss(tmp_path):
    """All messages are preserved even under load."""
    path = tmp_path / "trace.bin"
    count = 5000

    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        for i in range(count):
            w(i)
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        vals = _read_values(r)

    assert len(vals) == count
    for i, v in enumerate(vals):
        assert v == i


# ---------------------------------------------------------------------------
# In-flight byte accounting
# ---------------------------------------------------------------------------

def test_inflight_limit_default(tmp_path):
    """Default inflight_limit is 128 MB."""
    path = tmp_path / "trace.bin"
    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        assert w.queue.inflight_limit == 128 * 1024 * 1024


def test_inflight_limit_configurable(tmp_path):
    """inflight_limit can be set via constructor and property."""
    path = tmp_path / "trace.bin"
    with stream.writer(path, thread=_thread_id, inflight_limit=1024, format="unframed_binary") as w:
        assert w.queue.inflight_limit == 1024
        w.queue.inflight_limit = 2048
        assert w.queue.inflight_limit == 2048


def test_inflight_bytes_tracks_data(tmp_path):
    """inflight_bytes increases as data is written and returns to ~0 after flush."""
    import time
    path = tmp_path / "trace.bin"
    with stream.writer(path, thread=_thread_id, format="unframed_binary") as w:
        baseline = w.queue.inflight_bytes
        big = b"X" * 10000
        w(big)
        assert w.queue.inflight_bytes > baseline
        w.flush()
        time.sleep(0.2)
        assert w.queue.inflight_bytes < 1000


def test_inflight_no_data_loss_under_pressure(tmp_path):
    """With a low inflight limit, all messages still round-trip correctly."""
    path = tmp_path / "trace.bin"
    count = 500

    with stream.writer(path, thread=_thread_id, inflight_limit=4096, format="unframed_binary") as w:
        for i in range(count):
            w(f"pressure_{i:05d}")
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        vals = _read_values(r)

    assert len(vals) == count
    for i, v in enumerate(vals):
        assert v == f"pressure_{i:05d}", f"Mismatch at index {i}: {v!r}"


def test_push_fail_callback_disables_objectwriter():
    """A falsy push-fail callback should disable further native writer pushes."""
    callbacks = []

    queue = stream._backend_mod.Queue(
        queue_capacity=1,
        push_fail_callback=lambda: callbacks.append("fail"),
    )
    writer = stream._backend_mod.ObjectWriter(queue)

    try:
        writer.bind(object())
        assert callbacks == ["fail"]

        writer.bind(object())
        assert callbacks == ["fail"]
    finally:
        queue.close()
