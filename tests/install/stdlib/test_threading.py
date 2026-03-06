"""Test deterministic thread record/replay.

Verifies that multi-threaded code using patched functions produces the
same results on replay as during recording, and that thread interleaving
is captured correctly.

Skipped at the install level: _MemoryWriter/_MemoryReader lack per-thread
demuxing (ThreadSwitch).  Multi-threaded record/replay should be tested at
the retracesoftware level where the full production stream infrastructure
is available.
"""
import threading

import pytest

from retracesoftware.proxy.system import System
from retracesoftware.install import TestRunner

pytestmark = pytest.mark.skip(
    reason="_MemoryWriter/_MemoryReader lack ThreadSwitch demuxing — "
           "test multi-threaded replay at the retracesoftware level"
)


# ── helpers ────────────────────────────────────────────────────────

def _make_counter(sys):
    """Return a patched function that increments and returns a counter.

    Each call returns a new integer (1, 2, 3, ...).  The function is
    patched via system.patch so that every invocation crosses the
    sandbox boundary and is recorded/replayed.
    """
    state = {'n': 0}

    def _next():
        state['n'] += 1
        return state['n']

    return sys.patch(_next)


def _fresh_runner():
    """Create a standalone System + TestRunner pair.

    Each test that uses a counter needs its own system so state
    doesn't leak between record and replay.  Thread patching is
    handled by TestRunner.record/replay, not here.
    """
    s = System()
    s.immutable_types.update({
        int, float, str, bytes, bool, type, type(None),
        tuple, list, dict, set, frozenset,
    })
    return s, TestRunner(s)


# ── single-threaded ───────────────────────────────────────────────

def test_single_thread_multiple_calls():
    """Multiple calls in one thread replay in order."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        return [counter() for _ in range(5)]

    result = r.run(work)
    assert result == [1, 2, 3, 4, 5]


def test_single_thread_interleaved_functions():
    """Two different patched functions interleaved in one thread."""
    s, r = _fresh_runner()
    counter_a = _make_counter(s)
    counter_b = _make_counter(s)

    def work():
        return [counter_a(), counter_b(), counter_a(), counter_b()]

    result = r.run(work)
    assert result == [1, 1, 2, 2]


def test_single_thread_high_call_count():
    """Many calls in one thread replay correctly."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        return [counter() for _ in range(100)]

    result = r.run(work)
    assert result == list(range(1, 101))


# ── multi-threaded ────────────────────────────────────────────────

def test_two_threads_join():
    """Two threads that each call a patched function, joined by main."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        results = {}

        def thread_fn(name, count):
            results[name] = [counter() for _ in range(count)]

        t1 = threading.Thread(target=thread_fn, args=('a', 3))
        t2 = threading.Thread(target=thread_fn, args=('b', 3))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        return results

    result = r.run(work)
    all_values = sorted(result['a'] + result['b'])
    assert all_values == [1, 2, 3, 4, 5, 6]


def test_thread_returning_patched_value():
    """A thread can return a value obtained from a patched call."""
    import time
    s, r = _fresh_runner()
    patched_time = s.patch(time.time)

    def work():
        box = {}

        def thread_fn():
            box['t'] = patched_time()

        t = threading.Thread(target=thread_fn)
        t.start()
        t.join()
        return box['t']

    result = r.run(work)
    assert isinstance(result, float)


def test_many_threads_stress():
    """Ten threads each making several patched calls."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        results = [None] * 10

        def thread_fn(idx):
            results[idx] = [counter() for _ in range(5)]

        threads = []
        for i in range(10):
            t = threading.Thread(target=thread_fn, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        return [v for sublist in results for v in sublist]

    result = r.run(work)
    assert sorted(result) == list(range(1, 51))


def test_sequential_thread_creation():
    """Threads created and joined one at a time."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        values = []
        for _ in range(3):
            box = {}

            def thread_fn(b=box):
                b['v'] = counter()

            t = threading.Thread(target=thread_fn)
            t.start()
            t.join()
            values.append(box['v'])
        return values

    result = r.run(work)
    assert result == [1, 2, 3]


def test_main_and_child_interleaved():
    """Main thread and child thread both call patched functions."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        barrier = threading.Event()
        done = threading.Event()
        child_results = []

        def thread_fn():
            barrier.wait()
            child_results.append(counter())
            child_results.append(counter())
            done.set()

        t = threading.Thread(target=thread_fn)
        t.start()

        main_a = counter()
        barrier.set()
        done.wait()
        main_b = counter()

        t.join()
        return {'main': [main_a, main_b], 'child': child_results}

    result = r.run(work)
    all_values = sorted(result['main'] + result['child'])
    assert all_values == [1, 2, 3, 4]


def test_thread_with_exception():
    """A thread that raises still allows replay to succeed."""
    s, r = _fresh_runner()
    counter = _make_counter(s)

    def work():
        box = {'error': None, 'before': None}

        def thread_fn():
            box['before'] = counter()
            try:
                raise ValueError("boom")
            except ValueError as e:
                box['error'] = str(e)

        t = threading.Thread(target=thread_fn)
        t.start()
        t.join()
        return (box['before'], box['error'])

    result = r.run(work)
    assert result[0] == 1
    assert result[1] == "boom"
