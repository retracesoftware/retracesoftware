import sys
import threading

import pytest

_utils = pytest.importorskip("retracesoftware.utils")

requires_311 = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="CallCounter hooks require Python 3.11+",
)

requires_312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="JUMP monitoring requires Python 3.12+",
)


@pytest.fixture
def call_counter():
    """Provide a fresh CallCounter instance, cleaned up after the test."""
    cc = _utils.CallCounter()
    yield cc
    try:
        cc.uninstall()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_default():
    """Ensure the module-level default call counter is clean between tests."""
    yield
    try:
        _utils.uninstall_call_counter()
    except Exception:
        pass
    _utils.call_counter_reset()


# ============================================================================
# CallCounter type basics
# ============================================================================


class TestCallCounterType:
    def test_is_a_type(self):
        assert isinstance(_utils.CallCounter, type)

    def test_instantiation(self):
        cc = _utils.CallCounter()
        assert cc is not None

    def test_not_installed_by_default(self):
        cc = _utils.CallCounter()
        assert not cc.installed

    def test_repr_idle(self):
        cc = _utils.CallCounter()
        r = repr(cc)
        assert "CallCounter" in r
        assert "idle" in r

    @requires_311
    def test_repr_installed(self, call_counter):
        call_counter.install()
        r = repr(call_counter)
        assert "installed" in r

    def test_len_empty(self):
        cc = _utils.CallCounter()
        assert len(cc) == 0

    def test_depth_property(self):
        cc = _utils.CallCounter()
        assert cc.depth == 0


# ============================================================================
# Context manager
# ============================================================================


@requires_311
class TestContextManager:
    def test_basic_context_manager(self):
        cc = _utils.CallCounter()
        with cc:
            assert cc.installed

            def f():
                return cc.current()

            cur = f()
            assert len(cur) >= 1
        assert not cc.installed

    def test_context_manager_returns_self(self):
        cc = _utils.CallCounter()
        with cc as ctx:
            assert ctx is cc


# ============================================================================
# Install / uninstall
# ============================================================================


@requires_311
class TestInstallUninstall:
    def test_install_succeeds(self, call_counter):
        call_counter.install()
        assert call_counter.installed

    def test_double_install_is_noop(self, call_counter):
        call_counter.install()
        call_counter.install()
        assert call_counter.installed

    def test_uninstall_without_install_is_noop(self, call_counter):
        call_counter.uninstall()
        assert not call_counter.installed

    def test_install_then_uninstall(self, call_counter):
        call_counter.install()
        call_counter.uninstall()
        assert not call_counter.installed
        assert call_counter.current() == ()

    def test_reinstall_after_uninstall(self, call_counter):
        call_counter.install()
        call_counter.uninstall()
        call_counter.install()

        def f():
            return call_counter.current()

        cur = f()
        assert len(cur) > 0
        call_counter.uninstall()


# ============================================================================
# Basic call-count shape
# ============================================================================


@requires_311
class TestCallCountShape:
    def test_single_call_returns_tuple(self, call_counter):
        call_counter.install()

        def f():
            return call_counter.current()

        cur = f()
        assert isinstance(cur, tuple)
        assert len(cur) >= 1

    def test_each_entry_is_int(self, call_counter):
        call_counter.install()

        def f():
            return call_counter.current()

        for call_count in f():
            assert isinstance(call_count, int)

    def test_nested_calls_increase_depth(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def outer():
            return inner()

        c_inner = inner()
        c_outer = outer()
        assert len(c_outer) == len(c_inner) + 1

    def test_empty_after_uninstall(self, call_counter):
        call_counter.install()

        def f():
            return call_counter.current()

        assert len(f()) > 0

        call_counter.uninstall()
        assert call_counter.current() == ()


# ============================================================================
# Call counting (loops and sequential calls)
# ============================================================================


@requires_311
class TestCallCounting:
    def test_simple_loop_counts(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def with_loop():
            results = []
            for i in range(3):
                results.append(inner())
            return results

        cursors = with_loop()

        parent_counts = [c[0] for c in cursors]
        if sys.version_info >= (3, 12):
            # backward jumps also increment: call+backjump per iteration
            assert parent_counts == [1, 3, 5]
        else:
            assert parent_counts == [1, 2, 3]

        inner_counts = [c[1] for c in cursors]
        assert inner_counts == [0, 0, 0]

    def test_while_loop_counts(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def with_while():
            results = []
            i = 0
            while i < 4:
                results.append(inner())
                i += 1
            return results

        cursors = with_while()
        parent_counts = [c[0] for c in cursors]
        if sys.version_info >= (3, 12):
            assert parent_counts == [1, 3, 5, 7]
        else:
            assert parent_counts == [1, 2, 3, 4]

    def test_nested_loops(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def nested():
            results = []
            for i in range(2):
                for j in range(2):
                    results.append(inner())
            return results

        cursors = nested()
        assert len(cursors) == 4
        parent_counts = [c[0] for c in cursors]
        if sys.version_info >= (3, 12):
            # inner call + inner backjump per iter, plus outer backjump
            assert parent_counts == [1, 3, 6, 8]
        else:
            assert parent_counts == [1, 2, 3, 4]

    def test_sequential_calls_increment(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def sequential():
            a = inner()
            b = inner()
            return a, b

        a, b = sequential()
        assert a[-2] == 1
        assert b[-2] == 2
        assert a[-1] == 0
        assert b[-1] == 0

    @requires_312
    def test_loop_backjump_increments_call_count(self, call_counter):
        """Backward jumps (loop back-edges) should increment call_count even
        when the loop body contains only C-level calls (no Python functions)."""
        call_counter.install()

        def with_loop():
            results = []
            for i in range(3):
                results.append(call_counter.current())
            return results

        cursors = with_loop()
        loop_counts = [c[-1] for c in cursors]
        assert loop_counts == [0, 1, 2], (
            f"Each backward jump should increment call_count; got {loop_counts}"
        )

    @requires_312
    def test_while_backjump_increments_call_count(self, call_counter):
        """While-loop back-edges should also increment call_count."""
        call_counter.install()

        def with_while():
            results = []
            i = 0
            while i < 3:
                results.append(call_counter.current())
                i += 1
            return results

        cursors = with_while()
        loop_counts = [c[-1] for c in cursors]
        assert loop_counts == [0, 1, 2], (
            f"Each while-loop back-edge should increment call_count; got {loop_counts}"
        )

    @requires_312
    def test_nested_loop_backjumps(self, call_counter):
        """Nested loops: both inner and outer back-edges increment."""
        call_counter.install()

        def with_nested():
            results = []
            for i in range(2):
                for j in range(2):
                    results.append(call_counter.current())
            return results

        cursors = with_nested()
        counts = [c[-1] for c in cursors]
        assert counts[0] < counts[1] < counts[2] < counts[3], (
            f"Nested loop counts should be strictly increasing; got {counts}"
        )


# ============================================================================
# Call-count uniqueness
# ============================================================================


@requires_311
class TestCallCountUniqueness:
    def test_different_iterations_different_counts(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.current()

        def looped():
            return [inner() for _ in range(5)]

        cursors = looped()
        assert len(set(cursors)) == len(cursors), "Each iteration should produce unique call counts"

    def test_different_call_sites_different_counts(self, call_counter):
        call_counter.install()

        def probe():
            return call_counter.current()

        def caller():
            a = probe()
            b = probe()
            return a, b

        a, b = caller()
        assert a != b

    def test_c_callback_different_counts(self, call_counter):
        """C code calling Python callbacks (e.g. sorted with key=) must produce
        unique call counts even though the loop is in C, not Python bytecode."""
        call_counter.install()

        collected = []

        def capture(x):
            collected.append(call_counter.current())
            return x

        sorted([3, 1, 2], key=capture)
        assert len(collected) >= 2
        assert len(set(collected)) == len(collected), (
            "Each C-level callback invocation should have unique call counts"
        )

    def test_recursive_calls_different_counts(self, call_counter):
        call_counter.install()

        collected = []

        def recurse(n):
            collected.append(call_counter.current())
            if n > 0:
                recurse(n - 1)

        recurse(3)
        assert len(collected) == 4
        assert len(set(collected)) == 4, "Each recursion depth should have unique call counts"


# ============================================================================
# Exception handling (PY_UNWIND)
# ============================================================================


@requires_311
class TestExceptionUnwind:
    def test_stack_balanced_after_exception(self, call_counter):
        call_counter.install()

        def thrower():
            raise ValueError("test")

        def catcher():
            try:
                thrower()
            except ValueError:
                pass
            return call_counter.current()

        cur = catcher()
        assert len(cur) >= 1

    def test_repeated_exceptions_dont_leak(self, call_counter):
        call_counter.install()

        def thrower():
            raise RuntimeError("oops")

        def run():
            for _ in range(10):
                try:
                    thrower()
                except RuntimeError:
                    pass
            return call_counter.current()

        cur = run()
        assert len(cur) <= 3

    def test_after_nested_exceptions(self, call_counter):
        call_counter.install()

        def inner():
            raise TypeError("inner")

        def middle():
            try:
                inner()
            except TypeError:
                raise ValueError("middle")

        def outer():
            try:
                middle()
            except ValueError:
                pass
            return call_counter.current()

        cur = outer()
        assert len(cur) >= 1


# ============================================================================
# Thread isolation
# ============================================================================


@requires_311
class TestThreadIsolation:
    def test_per_thread(self, call_counter):
        call_counter.install()
        results = {}

        def worker(name, depth):
            def nest(n):
                if n == 0:
                    return call_counter.current()
                return nest(n - 1)

            results[name] = nest(depth)

        t1 = threading.Thread(target=worker, args=("shallow", 2))
        t2 = threading.Thread(target=worker, args=("deep", 5))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results["deep"]) > len(results["shallow"])

    def test_main_thread_unaffected_by_child(self, call_counter):
        call_counter.install()

        def capture():
            return call_counter.current()

        main_before = capture()

        def child():
            def a():
                def b():
                    return call_counter.current()
                return b()
            return a()

        result = {}
        t = threading.Thread(target=lambda: result.update(child=child()))
        t.start()
        t.join()

        main_after = capture()
        assert len(main_before) == len(main_after)


# ============================================================================
# Reset behavior
# ============================================================================


@requires_311
class TestReset:
    def test_reset_clears_during_active_hooks(self, call_counter):
        call_counter.install()

        def f():
            call_counter.reset()
            return call_counter.current()

        cur = f()
        assert cur == ()

    def test_resumes_after_reset(self, call_counter):
        call_counter.install()

        call_counter.reset()

        def f():
            return call_counter.current()

        cur = f()
        assert len(cur) >= 1


# ============================================================================
# yield_at
# ============================================================================


@requires_311
class TestYieldAt:
    def _sequence(self, cc):
        values = []

        def mark():
            values.append(cc.current())

        mark()
        mark()
        mark()
        return values

    def test_triggers_once_on_matching_target(self, call_counter):
        call_counter.install()

        call_counter.reset()
        first_run = self._sequence(call_counter)
        target = first_run[1]

        hits = []

        def on_hit():
            hits.append(True)

        call_counter.reset()
        call_counter.yield_at(on_hit, threading.get_ident(), target)
        self._sequence(call_counter)

        assert hits == [True]

    def test_thread_id_filter(self, call_counter):
        call_counter.install()

        call_counter.reset()
        target = self._sequence(call_counter)[0]
        hits = []

        def on_hit():
            hits.append(True)

        call_counter.reset()
        call_counter.yield_at(on_hit, threading.get_ident() + 1, target)
        self._sequence(call_counter)

        assert hits == []


# ============================================================================
# Frame positions and position
# ============================================================================


@requires_311
class TestFramePositions:
    def test_aligned_with_call_counts(self, call_counter):
        call_counter.install()

        def inner():
            return (
                call_counter.current(),
                call_counter.frame_positions(),
            )

        cur, positions = inner()
        assert len(cur) == len(positions)

    def test_returns_tuple_of_ints(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.frame_positions()

        positions = inner()
        assert isinstance(positions, tuple)
        for lasti in positions:
            assert isinstance(lasti, int)


@requires_311
class TestPosition:
    def test_zips_counts_and_lastis(self, call_counter):
        call_counter.install()

        def inner():
            return call_counter.position()

        pos = inner()
        assert isinstance(pos, tuple)
        for call_count, lasti in pos:
            assert isinstance(call_count, int)
            assert isinstance(lasti, int)

    def test_different_call_sites_differ(self, call_counter):
        call_counter.install()

        def probe():
            return call_counter.position()

        def caller():
            a = probe()
            b = probe()
            return a, b

        a, b = caller()
        assert a != b


# ============================================================================
# Backward-compat module-level functions
# ============================================================================


@requires_311
class TestBackwardCompat:
    def test_install_uninstall_hooks(self):
        _utils.install_cursor_hooks()
        assert _utils.current_cursor is not None

        def f():
            return _utils.current_cursor()

        cur = f()
        assert isinstance(cur, tuple)
        assert len(cur) >= 1

        _utils.uninstall_cursor_hooks()

    def test_cursor_position(self):
        _utils.install_cursor_hooks()

        def inner():
            return _utils.cursor_position()

        pos = inner()
        assert isinstance(pos, tuple)
        for call_count, lasti in pos:
            assert isinstance(call_count, int)
            assert isinstance(lasti, int)

        _utils.uninstall_cursor_hooks()

    def test_yield_at_cursor(self):
        _utils.install_cursor_hooks()
        _utils.cursor_reset()

        hits = []

        def mark():
            return _utils.current_cursor()

        cursors = [mark(), mark(), mark()]
        target = cursors[1]

        def on_hit():
            hits.append(True)

        _utils.cursor_reset()
        _utils.yield_at_cursor(on_hit, threading.get_ident(), target)
        mark()
        mark()
        mark()

        _utils.uninstall_cursor_hooks()
        assert hits == [True]


# ============================================================================
# disable_for
# ============================================================================


@requires_311
class TestDisableFor:
    def test_returns_callable(self, call_counter):
        call_counter.install()

        def fn():
            pass

        wrapped = call_counter.disable_for(fn)
        assert callable(wrapped)

    def test_rejects_non_callable(self, call_counter):
        with pytest.raises(TypeError):
            call_counter.disable_for(42)

    def test_wrapped_repr(self, call_counter):
        def my_func():
            pass

        wrapped = call_counter.disable_for(my_func)
        assert "DisabledCallback" in repr(wrapped)

    def test_frozen_inside_disabled_callback(self, call_counter):
        call_counter.install()

        def outer():
            before = call_counter.current()

            def callback():
                return call_counter.current()

            wrapped = call_counter.disable_for(callback)
            inside = wrapped()
            after = call_counter.current()
            return before, inside, after

        before, inside, after = outer()
        assert inside == before, (
            "current() inside a disabled callback should return "
            "the frozen value from before the callback"
        )

    def test_no_stack_growth_during_disabled_callback(self, call_counter):
        call_counter.install()

        def outer():
            depth_before = len(call_counter)

            def callback():
                def deeply_nested():
                    return len(call_counter)
                return deeply_nested()

            wrapped = call_counter.disable_for(callback)
            depth_inside = wrapped()
            return depth_before, depth_inside

        depth_before, depth_inside = outer()
        assert depth_inside == depth_before, (
            "call-count stack should not grow while tracking is disabled"
        )

    def test_tracking_resumes_after_disabled_callback(self, call_counter):
        call_counter.install()

        results = []

        def gather():
            def callback():
                pass

            wrapped = call_counter.disable_for(callback)

            def inner():
                results.append(call_counter.current())

            inner()
            wrapped()
            inner()

        gather()
        assert len(results) == 2
        c1, c2 = results
        assert c1 != c2, "After disabled callback returns, tracking should resume and produce new values"

    def test_nested_disable_for(self, call_counter):
        call_counter.install()

        def outer():
            frozen = call_counter.current()

            def inner_cb():
                def even_deeper():
                    return call_counter.current()

                wrapped2 = call_counter.disable_for(even_deeper)
                return wrapped2()

            wrapped1 = call_counter.disable_for(inner_cb)
            result = wrapped1()
            return frozen, result

        frozen, nested_result = outer()
        assert nested_result == frozen, (
            "Nested disable_for should still return the outermost frozen value"
        )

    def test_reset_clears_suspend_state(self, call_counter):
        call_counter.install()

        def callback():
            call_counter.reset()
            return call_counter.current()

        wrapped = call_counter.disable_for(callback)
        result = wrapped()
        assert result == ()

    def test_wrapped_passes_args_and_return(self, call_counter):
        def adder(a, b):
            return a + b

        wrapped = call_counter.disable_for(adder)
        assert wrapped(3, 4) == 7

    def test_wrapped_passes_kwargs(self, call_counter):
        def fn(a, b=10):
            return a + b

        wrapped = call_counter.disable_for(fn)
        assert wrapped(1, b=20) == 21

    def test_wrapped_propagates_exceptions(self, call_counter):
        call_counter.install()

        def failing():
            raise ValueError("boom")

        wrapped = call_counter.disable_for(failing)
        with pytest.raises(ValueError, match="boom"):
            wrapped()

        def after():
            return call_counter.current()

        cur = after()
        assert len(cur) >= 1, "Tracking should resume even after exception in disabled callback"

    def test_module_level_disable_for(self):
        _utils.install_call_counter()

        def fn():
            return _utils.current_call_counts()

        def outer():
            before = _utils.current_call_counts()
            wrapped = _utils.call_counter_disable_for(fn)
            inside = wrapped()
            return before, inside

        before, inside = outer()
        assert inside == before

        _utils.uninstall_call_counter()


# ============================================================================
# Cursor data type
# ============================================================================


class TestCursorDataType:
    def test_is_frozen_dataclass(self):
        c = _utils.Cursor(thread_id=1, function_counts=(0, 3), f_lasti=42)
        assert c.thread_id == 1
        assert c.function_counts == (0, 3)
        assert c.f_lasti == 42

        with pytest.raises(AttributeError):
            c.thread_id = 2

    def test_f_lasti_defaults_to_none(self):
        c = _utils.Cursor(thread_id=1, function_counts=(0,))
        assert c.f_lasti is None

    def test_to_dict_with_f_lasti(self):
        c = _utils.Cursor(thread_id=1, function_counts=(0, 3), f_lasti=42)
        d = c.to_dict()
        assert d == {"thread_id": 1, "function_counts": [0, 3], "f_lasti": 42}

    def test_to_dict_without_f_lasti(self):
        c = _utils.Cursor(thread_id=1, function_counts=(0, 3))
        d = c.to_dict()
        assert d == {"thread_id": 1, "function_counts": [0, 3]}
        assert "f_lasti" not in d

    def test_from_dict_with_f_lasti(self):
        d = {"thread_id": 5, "function_counts": [10, 20], "f_lasti": 100}
        c = _utils.Cursor.from_dict(d)
        assert c.thread_id == 5
        assert c.function_counts == (10, 20)
        assert c.f_lasti == 100

    def test_from_dict_without_f_lasti(self):
        d = {"thread_id": 5, "function_counts": [10, 20]}
        c = _utils.Cursor.from_dict(d)
        assert c.f_lasti is None

    def test_roundtrip(self):
        original = _utils.Cursor(thread_id=7, function_counts=(1, 2, 3), f_lasti=50)
        restored = _utils.Cursor.from_dict(original.to_dict())
        assert restored == original

    def test_equality(self):
        a = _utils.Cursor(thread_id=1, function_counts=(0,), f_lasti=10)
        b = _utils.Cursor(thread_id=1, function_counts=(0,), f_lasti=10)
        assert a == b

    def test_inequality(self):
        a = _utils.Cursor(thread_id=1, function_counts=(0,), f_lasti=10)
        b = _utils.Cursor(thread_id=1, function_counts=(0,), f_lasti=20)
        assert a != b


@requires_311
class TestCursorSnapshot:
    def test_returns_cursor_instance(self):
        _utils.install_call_counter()

        def f():
            return _utils.cursor_snapshot()

        snap = f()
        assert isinstance(snap, _utils.Cursor)
        _utils.uninstall_call_counter()

    def test_has_thread_id(self):
        _utils.install_call_counter()
        import _thread

        def f():
            return _utils.cursor_snapshot()

        snap = f()
        assert snap.thread_id == _thread.get_ident()
        _utils.uninstall_call_counter()

    def test_has_function_counts(self):
        _utils.install_call_counter()

        def f():
            return _utils.cursor_snapshot()

        snap = f()
        assert isinstance(snap.function_counts, tuple)
        assert len(snap.function_counts) >= 1
        _utils.uninstall_call_counter()

    def test_has_f_lasti(self):
        _utils.install_call_counter()

        def f():
            return _utils.cursor_snapshot()

        snap = f()
        assert isinstance(snap.f_lasti, int)
        _utils.uninstall_call_counter()

    def test_to_dict_produces_valid_dict(self):
        _utils.install_call_counter()

        def f():
            return _utils.cursor_snapshot().to_dict()

        d = f()
        assert "thread_id" in d
        assert "function_counts" in d
        assert "f_lasti" in d
        assert isinstance(d["function_counts"], list)
        _utils.uninstall_call_counter()
