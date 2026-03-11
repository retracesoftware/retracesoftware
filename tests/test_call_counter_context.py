"""Tests for the CallCounter context manager API.

Verifies that:
- CallCounter() creates a process-wide tracker
- cc() returns a context that scopes cursor_stack (no thread_id needed)
- Breakpoint scan + watch navigate produces identical call counts
- Only target() goes inside the with blocks for count alignment
"""
import sys
import _thread
import threading

import pytest

from retracesoftware.cursor import CallCounter, Cursor, cursor_snapshot
from retracesoftware.utils.breakpoint import BreakpointSpec, install_breakpoint

requires_312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="Breakpoints require sys.monitoring (Python 3.12+)",
)


def _run_to_cursor_prev(function_counts):
    """Compute the function_counts to pass to run_to_return so that a
    subsequent next_instruction loop reaches *function_counts*.

    For [8,3,2] returns [8,3,1].
    For [8,3,0] returns [8,2,0].
    For [0]     returns None.
    """
    for i in range(len(function_counts) - 1, -1, -1):
        if function_counts[i] > 0:
            prev = list(function_counts[:i + 1])
            prev[i] -= 1
            while len(prev) < len(function_counts):
                prev.append(0)
            return prev
    return None


# ── target functions (defined at module level so __file__ is stable) ──

def _setup():
    pass

def _inner():
    a = "bar"       # line used for breakpoint
    return a

def _target():
    _setup()
    _inner()
    result = 10
    return result


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure call counter is fully reset between tests."""
    yield
    from retracesoftware.cursor import uninstall_call_counter
    try:
        uninstall_call_counter()
    except Exception:
        pass


@requires_312
class TestCallCounterContext:

    def test_context_consistent_counts(self):
        """Two consecutive contexts yield the same counts at the same point."""
        cc = CallCounter()

        counts_a = None
        def capture_a():
            nonlocal counts_a
            counts_a = cc().current()

        counts_b = None
        def capture_b():
            nonlocal counts_b
            counts_b = cc().current()

        with cc():
            _target()
            cc.disable_for(capture_a)()

        with cc():
            _target()
            cc.disable_for(capture_b)()

        assert counts_a == counts_b

    def test_two_contexts_same_counts(self):
        """Two consecutive contexts produce identical counts for the same
        function call at the same relative position."""
        cc = CallCounter()

        counts_1 = None
        def capture_1():
            nonlocal counts_1
            counts_1 = cc().current()

        counts_2 = None
        def capture_2():
            nonlocal counts_2
            counts_2 = cc().current()

        def target_with_capture(capture_fn):
            capture_fn()
            _inner()

        with cc() as ctx:
            target_with_capture(cc.disable_for(capture_1))

        with cc() as ctx:
            target_with_capture(cc.disable_for(capture_2))

        assert counts_1 == counts_2
        assert len(counts_1) > 0

    def test_snapshot_returns_cursor(self):
        """ctx.snapshot() returns a Cursor with the right thread_id."""
        cc = CallCounter()
        tid = _thread.get_ident()
        snap = None

        def capture():
            nonlocal snap
            snap = cursor_snapshot()

        with cc() as ctx:
            cc.disable_for(capture)()

        assert snap is not None
        assert isinstance(snap, Cursor)
        assert snap.thread_id == tid

    def test_watch_on_return_fires(self):
        """A watch armed before entering the context fires inside it."""
        cc = CallCounter()

        entry_counts = None
        def capture_entry():
            nonlocal entry_counts
            entry_counts = cc().current()

        def probe(fn):
            fn()

        with cc() as ctx:
            probe(cc.disable_for(capture_entry))
            _inner()

        assert entry_counts is not None
        assert len(entry_counts) > 0, "entry_counts should be non-empty inside probe()"

        returned = []
        def on_return():
            returned.append(cursor_snapshot())

        ctx = cc()
        ctx.add_watch(tuple(entry_counts), on_return=cc.disable_for(on_return))
        with ctx:
            probe(cc.disable_for(lambda: None))
            _inner()

        assert len(returned) == 1, f"on_return did not fire; returned={returned}"

    def test_watch_callback_sees_exact_target(self):
        """Inside a watch callback, current() must equal the armed target."""
        cc = CallCounter()

        target_counts = None
        def capture_target():
            nonlocal target_counts
            target_counts = cc().current()

        def probe():
            cc.disable_for(capture_target)()

        with cc():
            probe()

        assert target_counts is not None and len(target_counts) > 0

        actual_at_fire = None
        def on_start():
            nonlocal actual_at_fire
            actual_at_fire = cc().current()

        target = tuple(target_counts)
        ctx = cc()
        ctx.add_watch(target, on_start=cc.disable_for(on_start))
        with ctx:
            probe()

        assert actual_at_fire is not None, "on_start callback did not fire"
        assert tuple(actual_at_fire) == target, (
            f"current() in callback {actual_at_fire} != target {target}"
        )

    def test_breakpoint_scan_then_navigate(self):
        """Full flow: scan for a breakpoint, then navigate to it using
        run_to_return + verify cursor alignment.

        Phase 1: install breakpoint on _inner's body line, run _target(),
                 capture the cursor.
        Phase 2: arm watch at prev(cursor.function_counts), run _target(),
                 verify the on_return callback fires.
        """
        cc = CallCounter()

        bp_cursor = None
        def on_bp_hit(frame):
            nonlocal bp_cursor
            if bp_cursor is None:
                bp_cursor = cursor_snapshot().to_dict()

        bp_line = _inner.__code__.co_firstlineno + 1
        monitor = install_breakpoint(
            BreakpointSpec(file=__file__, line=bp_line),
            cc.disable_for(on_bp_hit),
            disable_for=cc.disable_for,
        )

        with cc() as ctx:
            _target()
        monitor.close()

        assert bp_cursor is not None, "Breakpoint was not hit during scan"
        fc = bp_cursor["function_counts"]
        assert len(fc) >= 2, (
            f"Breakpoint inside _inner should have depth >= 2; got {fc}"
        )

        return_cursor = None
        def on_return():
            nonlocal return_cursor
            return_cursor = cursor_snapshot().to_dict()

        prev = _run_to_cursor_prev(fc)
        assert prev is not None, f"Could not compute prev for {fc}"

        ctx = cc()
        ctx.add_watch(tuple(prev), on_return=cc.disable_for(on_return))
        with ctx:
            _target()

        assert return_cursor is not None, (
            f"on_return did not fire; prev={prev} target_fc={fc}"
        )
        rfc = return_cursor["function_counts"]
        assert list(rfc) == list(prev), (
            f"on_return fires pre-pop, cursor should match watch target; "
            f"return_fc={rfc} prev={prev}"
        )

    def test_on_thread_switch_fires_across_threads(self):
        """on_thread_switch fires when a different thread enters monitoring."""
        cc = CallCounter()
        switches = []
        cc.on_thread_switch = cc.disable_for(lambda: switches.append(True))

        cc.install()
        with cc() as ctx:
            _target()

        assert len(switches) == 0, "no switch expected on single thread"

        barrier = threading.Barrier(2, timeout=5)
        done = threading.Event()

        def worker():
            with cc() as ctx:
                barrier.wait()
                _target()
            done.set()

        with cc() as ctx:
            t = threading.Thread(target=worker)
            t.start()
            barrier.wait()
            done.wait(timeout=5)
            _target()

        t.join(timeout=5)
        assert len(switches) >= 1, (
            f"expected on_thread_switch to fire at least once; got {len(switches)}"
        )
        cc.on_thread_switch = None

    def test_on_missed_fires_on_start_overshoot(self):
        """on_missed fires when on_start target is overshot (cur > tgt)."""
        cc = CallCounter()

        missed = []
        started = []
        bogus = (0, 999)

        ctx = cc()
        ctx.add_watch(
            bogus,
            on_start=cc.disable_for(lambda: started.append(True)),
            on_missed=cc.disable_for(lambda: missed.append(True)),
        )
        with ctx:
            _target()

        assert len(started) == 0, "on_start should not fire for bogus target"
        assert len(missed) == 1, (
            f"on_missed should fire once when target is overshot; got {len(missed)}"
        )

    def test_on_missed_fires_on_context_exit(self):
        """on_missed fires when context exits with armed slots still pending."""
        cc = CallCounter()

        missed = []
        returned = []
        unreachable = (0, 0, 0, 999)

        ctx = cc()
        ctx.add_watch(
            unreachable,
            on_return=cc.disable_for(lambda: returned.append(True)),
            on_missed=cc.disable_for(lambda: missed.append(True)),
        )
        with ctx:
            _target()

        assert len(returned) == 0, "on_return should not fire for unreachable target"
        assert len(missed) == 1, (
            f"on_missed should fire on context exit; got {len(missed)}"
        )

    # ── disable_for ──────────────────────────────────────────────────────

    def test_disable_for_preserves_counts(self):
        """Calls wrapped with disable_for do not perturb the cursor stack."""
        cc = CallCounter()

        counts_before = None
        counts_after = None

        def capture_before():
            nonlocal counts_before
            counts_before = cc._cc.current()

        def capture_after():
            nonlocal counts_after
            counts_after = cc._cc.current()

        def heavy_work():
            _inner()
            _inner()
            _inner()

        with cc() as ctx:
            cc.disable_for(capture_before)()
            cc.disable_for(heavy_work)()
            cc.disable_for(capture_after)()

        assert counts_before == counts_after, (
            f"disable_for should freeze counts: before={counts_before} after={counts_after}"
        )

    def test_disable_for_nesting(self):
        """Nested disable_for calls maintain correct suspend depth."""
        cc = CallCounter()

        inner_counts = None
        def nested():
            nonlocal inner_counts
            cc.disable_for(lambda: None)()
            inner_counts = cc._cc.current()

        with cc() as ctx:
            _inner()
            cc.disable_for(nested)()

        assert inner_counts is not None

    # ── watch on_start ───────────────────────────────────────────────────

    def test_watch_on_start_fires(self):
        """on_start fires when cursor reaches the exact target position."""
        cc = CallCounter()

        entry_counts = None
        def target_with_capture(capture_fn):
            capture_fn()
            _inner()

        def cap():
            nonlocal entry_counts
            entry_counts = cc._cc.current()

        with cc() as ctx:
            target_with_capture(cc.disable_for(cap))

        assert entry_counts is not None and len(entry_counts) > 0

        started = []
        ctx = cc()
        ctx.add_watch(
            tuple(entry_counts),
            on_start=cc.disable_for(lambda: started.append(True)),
        )
        with ctx:
            target_with_capture(cc.disable_for(lambda: None))

        assert len(started) >= 1, f"on_start should fire; got {len(started)}"

    # ── watch on_unwind ──────────────────────────────────────────────────

    def test_watch_on_unwind_fires_on_exception(self):
        """on_unwind fires when a function raises an exception."""
        cc = CallCounter()

        def raiser(fn):
            fn()
            raise ValueError("boom")

        entry_counts = None
        def capture_entry():
            nonlocal entry_counts
            entry_counts = cc().current()

        with cc() as ctx:
            try:
                raiser(cc.disable_for(capture_entry))
            except ValueError:
                pass

        assert entry_counts is not None and len(entry_counts) > 0

        unwound = []
        ctx = cc()
        ctx.add_watch(
            tuple(entry_counts),
            on_unwind=cc.disable_for(lambda: unwound.append(True)),
        )
        with ctx:
            try:
                raiser(cc.disable_for(lambda: None))
            except ValueError:
                pass

        assert len(unwound) >= 1, f"on_unwind should fire; got {len(unwound)}"

    # ── watch on_backjump ────────────────────────────────────────────────

    def test_watch_on_backjump_fires_in_loop(self):
        """on_backjump fires on a backward jump (loop iteration)."""
        cc = CallCounter()

        looper_counts = None

        def looper_with_capture(capture_fn):
            capture_fn()
            for i in range(3):
                pass

        def cap():
            nonlocal looper_counts
            looper_counts = cc._cc.current()

        with cc() as ctx:
            looper_with_capture(cc.disable_for(cap))

        assert looper_counts is not None and len(looper_counts) > 0

        jumped = []
        ctx = cc()
        ctx.add_watch(
            tuple(looper_counts),
            on_backjump=cc.disable_for(lambda: jumped.append(True)),
        )
        with ctx:
            looper_with_capture(cc.disable_for(lambda: None))

        assert len(jumped) >= 1, f"on_backjump should fire at least once; got {len(jumped)}"

    # ── position() ───────────────────────────────────────────────────────

    def test_position_returns_count_lasti_pairs(self):
        """position() returns a tuple of (count, f_lasti) pairs."""
        cc = CallCounter()

        pos = None
        def target_with_capture(capture_fn):
            capture_fn()
            _inner()

        def cap():
            nonlocal pos
            pos = cc().position()

        with cc() as ctx:
            target_with_capture(cc.disable_for(cap))

        assert pos is not None
        assert len(pos) > 0, f"position() should be non-empty inside a function; got {pos}"
        for pair in pos:
            assert len(pair) == 2, f"Each entry should be (count, f_lasti); got {pair}"
            count, lasti = pair
            assert isinstance(count, int)
            assert isinstance(lasti, int)

    # ── breakpoint with condition ─────────────────────────────────────────

    def test_breakpoint_with_condition(self):
        """Conditional breakpoints only fire when the condition is true."""
        cc = CallCounter()

        def counter_fn(n):
            x = n * 2
            return x

        hits = []
        def on_bp_hit(cursor_dict):
            hits.append(cursor_dict)

        bp_line = counter_fn.__code__.co_firstlineno + 1
        monitor = install_breakpoint(
            BreakpointSpec(file=__file__, line=bp_line, condition="n > 2"),
            on_bp_hit,
        )

        with cc() as ctx:
            counter_fn(1)
            counter_fn(2)
            counter_fn(3)
            counter_fn(4)
        monitor.close()

        assert len(hits) == 2, (
            f"Condition 'n > 2' should match n=3 and n=4; got {len(hits)} hits"
        )

    # ── install_function_breakpoint ──────────────────────────────────────

    def test_function_breakpoint_fires(self):
        """install_function_breakpoint fires when the target is called."""
        from retracesoftware.utils.breakpoint import install_function_breakpoint
        cc = CallCounter()

        hits = []
        def on_hit(cursor_dict):
            hits.append(cursor_dict)

        monitor = install_function_breakpoint(_inner, on_hit)

        with cc() as ctx:
            _target()
        monitor.close()

        assert len(hits) == 1, f"Function breakpoint should fire once; got {len(hits)}"

    # ── on_missed does NOT fire on success ────────────────────────────────

    def test_on_missed_does_not_fire_on_success(self):
        """on_missed should not fire when a watch callback fires normally."""
        cc = CallCounter()

        entry_counts = None
        def capture():
            nonlocal entry_counts
            entry_counts = cc._cc.current()

        def probe(fn):
            fn()

        with cc() as ctx:
            probe(cc.disable_for(capture))
            _inner()

        assert entry_counts is not None
        assert len(entry_counts) > 0

        returned = []
        missed = []
        ctx = cc()
        ctx.add_watch(
            tuple(entry_counts),
            on_return=cc.disable_for(lambda: returned.append(True)),
            on_missed=cc.disable_for(lambda: missed.append(True)),
        )
        with ctx:
            probe(cc.disable_for(lambda: None))
            _inner()

        assert len(returned) == 1, f"on_return should fire; got {len(returned)}"
        assert len(missed) == 0, f"on_missed should NOT fire on success; got {len(missed)}"

    # ── per-thread watch independence ────────────────────────────────────

    def test_per_thread_watches_independent(self):
        """Watches on different threads don't interfere with each other."""
        cc = CallCounter()

        main_returned = []
        worker_returned = []
        barrier = threading.Barrier(2, timeout=5)

        def probe(fn):
            fn()

        def worker():
            entry = None
            def cap():
                nonlocal entry
                entry = cc._cc.current()

            with cc() as ctx:
                probe(cc.disable_for(cap))
                _inner()

            ctx2 = cc()
            ctx2.add_watch(
                tuple(entry),
                on_return=cc.disable_for(lambda: worker_returned.append(True)),
            )
            with ctx2:
                barrier.wait()
                probe(cc.disable_for(lambda: None))
                _inner()

        t = threading.Thread(target=worker)
        t.start()

        entry = None
        def cap():
            nonlocal entry
            entry = cc._cc.current()

        with cc() as ctx:
            probe(cc.disable_for(cap))
            _inner()

        ctx2 = cc()
        ctx2.add_watch(
            tuple(entry),
            on_return=cc.disable_for(lambda: main_returned.append(True)),
        )
        with ctx2:
            barrier.wait()
            probe(cc.disable_for(lambda: None))
            _inner()

        t.join(timeout=5)

        assert len(main_returned) == 1, (
            f"Main thread watch should fire; got {len(main_returned)}"
        )
        assert len(worker_returned) == 1, (
            f"Worker thread watch should fire; got {len(worker_returned)}"
        )

    # ── thread middleware auto-context ────────────────────────────────────

    def test_thread_middleware_gives_new_threads_context(self):
        """When CallCounter is installed, new threads automatically get
        cursor tracking via the thread middleware."""
        cc = CallCounter()
        cc.install()

        worker_counts = None
        done = threading.Event()

        def worker():
            nonlocal worker_counts
            def cap():
                nonlocal worker_counts
                worker_counts = cc._cc.current()
            _inner()
            cc.disable_for(cap)()
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=5)

        assert worker_counts is not None, (
            "Worker thread should have cursor tracking from middleware"
        )
