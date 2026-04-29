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


def _wait_for_waiters(dispatcher, count, timeout=5):
    deadline = time.monotonic() + timeout
    while dispatcher.waiting_thread_count < count and time.monotonic() < deadline:
        time.sleep(0.001)
    assert dispatcher.waiting_thread_count == count


class TestNextPredicate:
    """Basic next(predicate) dispatch."""

    def test_single_item(self):
        d = _make_dispatcher([(1, "hello")])
        result = d.next(lambda x: x[0] == 1)
        assert result == (1, "hello")

    def test_buffered_forces_lazy_load(self):
        """Accessing buffered loads the next pending item on demand."""
        d = _make_dispatcher([(1, "a"), (2, "b")])
        assert d.buffered == (1, "a")
        d.next(lambda x: x[0] == 1)
        assert d.buffered == (2, "b")

    def test_peek_returns_buffered_item_without_consuming(self):
        d = _make_dispatcher([(1, "a"), (2, "b")])
        assert d.peek() == (1, "a")
        assert d.peek() == (1, "a")
        assert d.next(lambda x: x[0] == 1) == (1, "a")
        assert d.peek() == (2, "b")

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

    def test_waiting_matching_thread_is_not_deadlock(self):
        """A waiter for the buffered item means another thread should keep waiting."""
        d = _make_dispatcher([
            (1, "worker-first"),
            (0, "main-middle"),
            (1, "worker-last"),
        ])
        ready = threading.Event()
        results = {}
        errors = []

        def waiting_for_main_item():
            ready.set()
            try:
                results["main"] = d.next(lambda item: item[0] == 0)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=waiting_for_main_item)
        t.start()
        assert ready.wait(timeout=5)

        _wait_for_waiters(d, 1)

        try:
            first = d.next(lambda item: item[0] == 1)
            second = d.next(lambda item: item[0] == 1)
        finally:
            t.join(timeout=5)

        assert not t.is_alive()
        assert errors == []
        assert first == (1, "worker-first")
        assert second == (1, "worker-last")
        assert results == {"main": (0, "main-middle")}
        assert d.waiting_thread_count == 0

    def test_repeated_waiting_matching_thread_handoffs_are_not_deadlock(self):
        d = _make_dispatcher([
            (1, "worker-1"),
            (0, "main-1"),
            (1, "worker-2"),
            (0, "main-2"),
            (1, "worker-3"),
        ])
        ready = threading.Event()
        main_results = []
        errors = []

        def waiting_for_main_items():
            try:
                ready.set()
                main_results.append(d.next(lambda item: item[0] == 0))
                main_results.append(d.next(lambda item: item[0] == 0))
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=waiting_for_main_items)
        t.start()
        assert ready.wait(timeout=5)
        _wait_for_waiters(d, 1)

        try:
            worker_results = [
                d.next(lambda item: item[0] == 1),
                d.next(lambda item: item[0] == 1),
                d.next(lambda item: item[0] == 1),
            ]
        finally:
            t.join(timeout=5)

        assert not t.is_alive()
        assert errors == []
        assert main_results == [(0, "main-1"), (0, "main-2")]
        assert worker_results == [
            (1, "worker-1"),
            (1, "worker-2"),
            (1, "worker-3"),
        ]
        assert d.waiting_thread_count == 0

    def test_wait_for_all_pending_returns_when_other_threads_are_waiting(self):
        d = _make_dispatcher([(0, "main"), (1, "worker")])
        ready = threading.Event()
        results = {}
        errors = []

        def waiting_for_worker_item():
            ready.set()
            try:
                results["worker"] = d.next(lambda item: item[0] == 1)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=waiting_for_worker_item)
        t.start()
        assert ready.wait(timeout=5)

        d.wait_for_all_pending()
        assert d.waiting_thread_count == 1

        results["main"] = d.next(lambda item: item[0] == 0)
        t.join(timeout=5)

        assert not t.is_alive()
        assert errors == []
        assert results == {
            "main": (0, "main"),
            "worker": (1, "worker"),
        }
        assert d.waiting_thread_count == 0

    def test_interrupt_runs_waiter_callback_and_restores_buffered_item(self):
        d = _make_dispatcher([(0, "main"), (1, "worker")])
        ready = threading.Event()
        callback_seen = threading.Event()
        callback_calls = []
        results = {}
        errors = []

        def waiting_for_worker_item():
            ready.set()
            try:
                results["worker"] = d.next(lambda item: item[0] == 1)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=waiting_for_worker_item)
        t.start()
        assert ready.wait(timeout=5)
        _wait_for_waiters(d, 1)
        assert d.buffered == (0, "main")

        def on_waiting_thread():
            callback_calls.append(threading.get_ident())
            callback_seen.set()

        def while_interrupted():
            assert callback_seen.wait(timeout=5)
            return "interrupted"

        assert d.interrupt(on_waiting_thread, while_interrupted) == "interrupted"
        assert len(callback_calls) == 1
        assert d.buffered == (0, "main")

        results["main"] = d.next(lambda item: item[0] == 0)
        t.join(timeout=5)

        assert not t.is_alive()
        assert errors == []
        assert results == {
            "main": (0, "main"),
            "worker": (1, "worker"),
        }
        assert d.waiting_thread_count == 0


class TestNoMatch:
    """Error detection when no thread's predicate matches the buffered item."""

    def test_single_thread_no_match(self):
        """One thread, predicate never matches -- RuntimeError."""
        d = _make_dispatcher([(99, "orphan")], deadlock_timeout_seconds=0.01)

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)

    def test_error_on_retry(self):
        """After error, subsequent next() calls also raise."""
        d = _make_dispatcher([(99, "orphan")], deadlock_timeout_seconds=0.01)

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda x: x[0] == 1)

    def test_multi_thread_no_match_still_errors_and_can_be_cleaned_up(self):
        d = _make_dispatcher([(99, "orphan")], deadlock_timeout_seconds=0.05)
        ready = threading.Event()
        errors = []

        def waiting_for_missing_item():
            ready.set()
            try:
                d.next(lambda item: item[0] == 1)
            except BaseException as exc:
                errors.append(exc)

        t = threading.Thread(target=waiting_for_missing_item)
        t.start()
        assert ready.wait(timeout=5)
        _wait_for_waiters(d, 1)

        with pytest.raises(RuntimeError, match="too many threads waiting"):
            d.next(lambda item: item[0] == 2)

        assert d.next(lambda item: item[0] == 99) == (99, "orphan")
        t.join(timeout=5)

        assert not t.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], (RuntimeError, StopIteration))
        assert d.waiting_thread_count == 0

    def test_unrelated_thread_does_not_hide_no_match_deadlock(self):
        d = _make_dispatcher([(99, "orphan")], deadlock_timeout_seconds=0.05)
        idle_stop = threading.Event()
        idle = threading.Thread(target=idle_stop.wait)
        idle.start()

        completed = threading.Event()
        lock = threading.Lock()
        errors = []
        results = []

        def waiting_for_missing_item(key):
            try:
                result = d.next(lambda item: item[0] == key)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            else:
                with lock:
                    results.append(result)
            finally:
                completed.set()

        t1 = threading.Thread(target=waiting_for_missing_item, args=(1,))
        t1.start()
        _wait_for_waiters(d, 1)

        t2 = threading.Thread(target=waiting_for_missing_item, args=(2,))
        t2.start()

        try:
            assert completed.wait(timeout=2), (
                "non-dispatcher thread hid Dispatcher no-match detection"
            )
            with lock:
                assert results == []
                assert any(
                    isinstance(exc, RuntimeError)
                    and "too many threads waiting" in str(exc)
                    for exc in errors
                )
        finally:
            try:
                d.next(lambda item: item[0] == 99)
            except (RuntimeError, StopIteration):
                pass
            idle_stop.set()
            idle.join(timeout=5)
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert not idle.is_alive()
        assert not t1.is_alive()
        assert not t2.is_alive()
        assert d.waiting_thread_count == 0

    def test_fresh_thread_can_take_item_before_deadlock_timeout(self):
        d = _make_dispatcher(
            [(99, "fresh"), (2, "after")],
            deadlock_timeout_seconds=1.0,
        )
        lock = threading.Lock()
        errors = []
        results = {}
        candidate_started = threading.Event()
        candidate_done = threading.Event()

        def waiting_for_missing_item():
            try:
                d.next(lambda item: item[0] == 1)
            except BaseException as exc:
                with lock:
                    errors.append(("waiter", exc))

        def waiting_for_later_item():
            candidate_started.set()
            try:
                result = d.next(lambda item: item[0] == 2)
            except BaseException as exc:
                with lock:
                    errors.append(("candidate", exc))
            else:
                with lock:
                    results["candidate"] = result
            finally:
                candidate_done.set()

        t1 = threading.Thread(target=waiting_for_missing_item)
        t1.start()
        _wait_for_waiters(d, 1)

        t2 = threading.Thread(target=waiting_for_later_item)
        t2.start()
        assert candidate_started.wait(timeout=5)
        assert not candidate_done.wait(timeout=0.05)

        results["fresh"] = d.next(lambda item: item[0] == 99)

        t2.join(timeout=5)
        t1.join(timeout=5)

        assert not t2.is_alive()
        assert not t1.is_alive()
        assert results == {
            "fresh": (99, "fresh"),
            "candidate": (2, "after"),
        }
        assert len(errors) == 1
        name, exc = errors[0]
        assert name == "waiter"
        assert isinstance(exc, StopIteration)
        assert d.waiting_thread_count == 0

    def test_deadlock_timeout_must_be_non_negative(self):
        with pytest.raises(ValueError, match="deadlock timeout"):
            _make_dispatcher([(99, "orphan")], deadlock_timeout_seconds=-1)


class TestTerminalState:
    def test_peek_replays_stop_iteration_after_eof(self):
        d = _make_dispatcher([(1, "done")])

        assert d.next(lambda x: x[0] == 1) == (1, "done")

        with pytest.raises(StopIteration):
            d.peek()

        with pytest.raises(StopIteration):
            d.buffered
