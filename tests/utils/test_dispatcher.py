"""Tests for Dispatcher: next(predicate), wait_for_all_pending.

Tests verify:
- next(predicate) dispatches items to the correct thread
- No-match error detection (raises RuntimeError when no predicate matches)
"""
import time
import threading

import pytest

_utils = pytest.importorskip("retracesoftware.utils")


def _make_dispatcher(events, **kwargs):
    """Create a Dispatcher backed by a list of events."""
    it = iter(events)

    def source():
        return next(it)

    return _utils.Dispatcher(source, **kwargs)


class TestNextPredicate:
    """Basic next(predicate) dispatch."""

    def test_single_item(self):
        d = _make_dispatcher([(1, "hello")])
        result = d.next(lambda x: x[0] == 1)
        assert result == (1, "hello")

    def test_buffered_raises_when_empty(self):
        """Accessing buffered raises RuntimeError when nothing is buffered."""
        d = _make_dispatcher([(1, "a"), (2, "b")])
        d.next(lambda x: x[0] == 1)
        with pytest.raises(RuntimeError, match="no item currently buffered"):
            d.buffered

    def test_two_threads_dispatch(self):
        """Two threads each get their own events via predicates."""
        events = [(1, "a"), (2, "b"), (1, "c"), (2, "d")]

        it = iter(events)
        started = False
        def source():
            nonlocal started
            if not started:
                time.sleep(0.2)
                started = True
            return next(it)

        d = _utils.Dispatcher(source)
        results = {1: [], 2: []}

        def worker(tid, count):
            try:
                for _ in range(count):
                    item = d.next(lambda x, t=tid: x[0] == t)
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


class TestNoMatch:
    """Error detection when no thread's predicate matches the buffered item."""

    def test_single_thread_no_match(self):
        """One thread, predicate never matches -- RuntimeError."""
        d = _make_dispatcher([(99, "orphan")])

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)

    def test_error_on_retry(self):
        """After error, subsequent next() calls also raise."""
        d = _make_dispatcher([(99, "orphan")])

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)
