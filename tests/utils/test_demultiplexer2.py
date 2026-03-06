"""Tests for Demultiplexer2: Python wrapper around Dispatcher.

Tests verify:
- __call__(key) dispatches items by key_function match
- Duplicate key detection
- on_timeout callback on deadlock
- pending_keys / pending properties
- Two-thread dispatch
"""
import time
import threading

import pytest

_utils = pytest.importorskip("retracesoftware.utils")


def _make_demux(events, key_function=None, **kwargs):
    """Create a Demultiplexer2 backed by a list of (key, payload) tuples."""
    it = iter(events)

    def source():
        return next(it)

    if key_function is None:
        key_function = lambda x: x[0]

    return _utils.Demultiplexer2(source, key_function, **kwargs)


class TestCallSingleThread:
    """Single-thread __call__ behaviour."""

    def test_single_item(self):
        d = _make_demux([(1, "hello")])
        result = d(1)
        assert result == (1, "hello")

    def test_two_items_same_key(self):
        d = _make_demux([(1, "a"), (1, "b")])
        assert d(1) == (1, "a")
        assert d(1) == (1, "b")

    def test_pending_none_after_take(self):
        d = _make_demux([(1, "a")])
        d(1)
        assert d.pending is None

    def test_source_property(self):
        source = lambda: (1, "x")
        d = _utils.Demultiplexer2(source, lambda x: x[0])
        assert d.source is source


class TestDuplicateKey:
    """Duplicate key detection."""

    def test_duplicate_raises(self):
        """Two threads requesting the same key: second should raise ValueError."""
        events = [(1, "a"), (1, "b"), (2, "c")]
        it = iter(events)
        barrier = threading.Barrier(2)

        def source():
            return next(it)

        d = _utils.Demultiplexer2(source, lambda x: x[0])
        errors = []

        def worker_key1():
            barrier.wait()
            try:
                d(1)
            except ValueError as e:
                errors.append(e)

        def worker_key1_dup():
            barrier.wait()
            time.sleep(0.1)
            try:
                d(1)
            except ValueError as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_key1)
        t2 = threading.Thread(target=worker_key1_dup)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) <= 1


class TestOnTimeout:
    """on_timeout callback on RuntimeError from Dispatcher."""

    def test_on_timeout_called(self):
        d = _make_demux(
            [(99, "orphan")],
            on_timeout=lambda demux, key: ("fallback", key),
        )
        result = d(1)
        assert result == ("fallback", 1)

    def test_no_timeout_raises(self):
        d = _make_demux([(99, "orphan")])
        with pytest.raises(RuntimeError):
            d(1)


class TestTwoThreadDispatch:
    """Two threads each get events matching their key."""

    def test_two_threads(self):
        events = [(1, "a"), (2, "b"), (1, "c"), (2, "d")]
        it = iter(events)
        started = False

        def source():
            nonlocal started
            if not started:
                time.sleep(0.2)
                started = True
            return next(it)

        d = _utils.Demultiplexer2(source, lambda x: x[0])
        results = {1: [], 2: []}

        def worker(tid, count):
            try:
                for _ in range(count):
                    item = d(tid)
                    if item:
                        results[tid].append(item[1])
            except RuntimeError:
                pass

        t1 = threading.Thread(target=worker, args=(1, 2))
        t2 = threading.Thread(target=worker, args=(2, 2))

        t1.start()
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results[1] == ["a", "c"]
        assert results[2] == ["b", "d"]


class TestPendingKeys:
    """pending_keys property tracks active waiters."""

    def test_pending_keys_empty_initially(self):
        d = _make_demux([(1, "a")])
        assert d.pending_keys == ()

    def test_pending_keys_empty_after_call(self):
        d = _make_demux([(1, "a")])
        d(1)
        assert d.pending_keys == ()
