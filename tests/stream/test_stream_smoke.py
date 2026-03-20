import pickle

import pytest

# 1. Check if the top-level package even exists
try:
    import retracesoftware.stream
    print(f"DEBUG: Found top-level at {retracesoftware.stream.__file__}")
except ImportError as e:
    print(f"DEBUG: Could not find retracesoftware_stream: {e}")
    
# Skip if the compiled extension (or its deps) is not available.
pytest.importorskip("retracesoftware.stream")
stream = pytest.importorskip("retracesoftware.stream")


def _thread_id() -> str:
    return "main-thread"


def _read_value(reader):
    """Read the next non-control value from reader."""
    while True:
        val = reader()
        if not isinstance(val, stream.Control):
            return val


def test_writer_reader_roundtrip(tmp_path):
    """Basic roundtrip with strings and integers."""
    path = tmp_path / "trace.bin"

    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        writer("hello", 123)
        writer.flush()

    with stream.reader(path, read_timeout=1, verbose=False) as reader:
        assert _read_value(reader) == "hello"
        assert _read_value(reader) == 123


def test_primitive_types(tmp_path):
    """Test various primitive types roundtrip correctly."""
    path = tmp_path / "trace.bin"
    
    test_values = [
        # Integers
        0, 1, -1, 42, -999,
        2**31 - 1,   # max int32
        -(2**31),    # min int32
        2**63 - 1,   # max int64
        # Floats
        0.0, 1.5, -3.14159, 1e-10, 1e100,
        float('inf'), float('-inf'),
        # Strings
        "", "a", "hello world", "unicode: 你好 🎉",
        "a" * 1000,  # longer string
        # None
        None,
        # Booleans
        True, False,
    ]
    
    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        for val in test_values:
            writer(val)
        writer.flush()
    
    with stream.reader(path, read_timeout=1, verbose=False) as reader:
        for expected in test_values:
            actual = _read_value(reader)
            if expected != expected:  # NaN check
                assert actual != actual
            else:
                assert actual == expected, f"Expected {expected!r}, got {actual!r}"


def test_collections(tmp_path):
    """Test lists, tuples, dicts, and sets roundtrip correctly."""
    path = tmp_path / "trace.bin"
    
    test_values = [
        # Lists
        [], [1], [1, 2, 3], ["a", "b", "c"],
        [1, "mixed", 3.14, None, True],
        [[1, 2], [3, 4]],  # nested
        # Tuples
        (), (1,), (1, 2, 3), ("a", "b"),
        (1, (2, (3,))),  # nested
        # Dicts
        {}, {"a": 1}, {"x": 1, "y": 2, "z": 3},
        {"nested": {"inner": 42}},
        {1: "int key", "str": "str key"},
        # Sets
        set(), {1}, {1, 2, 3}, {"a", "b", "c"},
        frozenset(), frozenset({1, 2, 3}),
    ]
    
    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        for val in test_values:
            writer(val)
        writer.flush()
    
    with stream.reader(path, read_timeout=1, verbose=False) as reader:
        for expected in test_values:
            actual = _read_value(reader)
            assert actual == expected, f"Expected {expected!r}, got {actual!r}"


def test_bytes(tmp_path):
    """Test bytes and bytearray roundtrip correctly."""
    path = tmp_path / "trace.bin"
    
    test_values = [
        b"",
        b"hello",
        b"\x00\x01\x02\xff",
        bytes(range(256)),
        b"x" * 10000,
    ]
    
    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        for val in test_values:
            writer(val)
        writer.flush()
    
    with stream.reader(path, read_timeout=1, verbose=False) as reader:
        for expected in test_values:
            actual = _read_value(reader)
            assert actual == expected, f"Expected {expected!r}, got {actual!r}"


def test_multiple_writes_single_call(tmp_path):
    """Test writing multiple values in a single call."""
    path = tmp_path / "trace.bin"
    
    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        writer("a", "b", "c", 1, 2, 3)
        writer.flush()
    
    with stream.reader(path, read_timeout=1, verbose=False) as reader:
        assert _read_value(reader) == "a"
        assert _read_value(reader) == "b"
        assert _read_value(reader) == "c"
        assert _read_value(reader) == 1
        assert _read_value(reader) == 2
        assert _read_value(reader) == 3


def test_large_data(tmp_path):
    """Test with larger amounts of data."""
    path = tmp_path / "trace.bin"
    
    # Write many values
    num_values = 1000
    with stream.writer(path, thread=_thread_id, flush_interval=0.01, format="unframed_binary") as writer:
        for i in range(num_values):
            writer(i, f"string_{i}", [i, i+1, i+2])
        writer.flush()
    
    with stream.reader(path, read_timeout=5, verbose=False) as reader:
        for i in range(num_values):
            assert _read_value(reader) == i
            assert _read_value(reader) == f"string_{i}"
            assert _read_value(reader) == [i, i+1, i+2]


@pytest.mark.skip(reason="Python callback output not supported in SPSC queue architecture")
def test_in_memory_callback(tmp_path):
    """Test that the writer can use a custom in-memory callback instead of a file."""
    import os, struct as _struct
    chunks = []

    with stream.writer(output=lambda data: chunks.append(bytes(data)),
                       thread=_thread_id, flush_interval=0.01) as w:
        w(42, "hello", [1, 2, 3])
        w.flush()

    # Verify we got some data
    assert len(chunks) > 0
    trace_bytes = b''.join(chunks)
    assert len(trace_bytes) > 0

    # Wrap raw callback data in PID frames so the reader can parse it
    pid = os.getpid()
    framed = bytearray()
    offset = 0
    while offset < len(trace_bytes):
        chunk = min(len(trace_bytes) - offset, 0xFFFF)
        framed.extend(_struct.pack('<IH', pid, chunk))
        framed.extend(trace_bytes[offset:offset + chunk])
        offset += chunk

    trace = tmp_path / "trace.bin"
    trace.write_bytes(bytes(framed))

    with stream.reader(path=trace, read_timeout=1, verbose=False) as r:
        assert _read_value(r) == 42
        assert _read_value(r) == "hello"
        assert _read_value(r) == [1, 2, 3]


@pytest.mark.skip(reason="Python callback output not supported in SPSC queue architecture")
def test_custom_output_roundtrip(tmp_path):
    """Full roundtrip using FileOutput explicitly."""
    path = tmp_path / "trace.bin"
    output = stream.FileOutput(path)

    with stream.writer(output=output, thread=_thread_id, flush_interval=0.01) as w:
        w("explicit", "file", "output")
        w.flush()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        assert _read_value(r) == "explicit"
        assert _read_value(r) == "file"
        assert _read_value(r) == "output"


def test_persister_explicit(tmp_path):
    """Test explicit construction of FramedWriter + Persister and roundtrip."""
    import retracesoftware.stream as st

    path = tmp_path / "async_trace.bin"
    fw = st._backend_mod.FramedWriter(str(path), raw=True)
    persister = st._backend_mod.Persister(fw, serializer=pickle.dumps)
    st._backend_mod.Queue(persister, thread=_thread_id)

    with stream.writer(output=persister, thread=_thread_id, flush_interval=0.01) as w:
        w("async", "persister", 42)
        w({"key": "value"}, [1, 2, 3])
        w.flush()

    fw.close()

    with stream.reader(path=path, read_timeout=1, verbose=False) as r:
        assert _read_value(r) == "async"
        assert _read_value(r) == "persister"
        assert _read_value(r) == 42
        assert _read_value(r) == {"key": "value"}
        assert _read_value(r) == [1, 2, 3]


def test_async_persister_large_data(tmp_path):
    """Test Persister with enough data to exercise double buffering."""
    path = tmp_path / "large_trace.bin"

    num_values = 5000
    with stream.writer(path, thread=_thread_id, flush_interval=0.005, format="unframed_binary") as w:
        for i in range(num_values):
            w(i, f"val_{i}")
        w.flush()

    with stream.reader(path=path, read_timeout=5, verbose=False) as r:
        for i in range(num_values):
            assert _read_value(r) == i
            assert _read_value(r) == f"val_{i}"
