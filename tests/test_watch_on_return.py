"""Regression tests for add_watch on_return with non-leaf functions.

The run_to_return control protocol uses cursor.watch(counters, on_return=...,
on_missed=...) to detect when a target function returns.  fire_exact in
watch_state.cpp demands an exact call_count match at every depth, but child
calls within the watched function increment the last entry's call_count,
so fire_exact never matches for any function that makes child calls.

The watch becomes a zombie — neither on_return nor on_overshoot fires, the
replay runs to completion, and the Go side sees an unexpected EOF.
"""
import sys

import pytest

import retracesoftware.cursor as _cursor

requires_312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="CallCounter hooks require Python 3.12+",
)


@pytest.fixture
def cc():
    """Provide a raw CallCounter, cleaned up after the test."""
    raw = _cursor._RawCallCounter()
    raw.install()
    yield raw
    try:
        raw.uninstall()
    except Exception:
        pass
    raw.reset()


class TestWatchOnReturnLeaf:
    """Baseline: on_return works for leaf functions (no child calls)."""

    @requires_312
    def test_on_return_fires_for_leaf_function(self, cc):
        captured = []

        def capturing_leaf():
            captured.append(cc.current())

        cc.reset()
        capturing_leaf()
        target = captured[0]

        on_return_fired = []
        on_missed_fired = []

        cc.reset()
        tc = cc()
        tc.add_watch(
            target,
            on_return=cc.disable_for(lambda: on_return_fired.append(True)),
            on_overshoot=cc.disable_for(lambda: on_missed_fired.append(True)),
        )

        def leaf():
            pass

        leaf()

        assert on_return_fired == [True], (
            f"on_return should fire for a leaf function; target={target}"
        )
        assert on_missed_fired == [], (
            f"on_overshoot should not fire; target={target}"
        )


class TestWatchOnReturnNonLeaf:
    """Regression: on_return must fire when a non-leaf function returns.

    After the function makes child calls, cursor_stack[-1].call_count is N (>0)
    while the target has 0, so fire_exact always fails.
    """

    @requires_312
    def test_on_return_fires_for_nonleaf_function(self, cc):
        def child():
            pass

        captured = []

        def nonleaf_capture():
            captured.append(cc.current())
            child()
            child()

        cc.reset()
        nonleaf_capture()
        target = captured[0]

        on_return_fired = []
        on_missed_fired = []

        cc.reset()
        tc = cc()
        tc.add_watch(
            target,
            on_return=cc.disable_for(lambda: on_return_fired.append(True)),
            on_overshoot=cc.disable_for(lambda: on_missed_fired.append(True)),
        )

        def nonleaf_replay():
            child()
            child()

        nonleaf_replay()

        assert on_missed_fired == [], (
            f"on_overshoot should NOT fire for normal child calls within "
            f"the watched function; target={target}"
        )
        assert on_return_fired == [True], (
            f"on_return should fire when the non-leaf function returns; "
            f"target={target}"
        )

    @requires_312
    def test_on_return_fires_for_deeply_nested_nonleaf(self, cc):
        """Same issue but with deeper nesting — mirrors real debugging
        scenarios like stepping back through functions with many subcalls."""

        def grandchild():
            pass

        def child():
            grandchild()
            grandchild()

        captured = []

        def nonleaf_capture():
            captured.append(cc.current())
            child()
            child()

        cc.reset()
        nonleaf_capture()
        target = captured[0]

        on_return_fired = []

        cc.reset()
        tc = cc()
        tc.add_watch(
            target,
            on_return=cc.disable_for(lambda: on_return_fired.append(True)),
        )

        def nonleaf_replay():
            child()
            child()

        nonleaf_replay()

        assert on_return_fired == [True], (
            f"on_return should fire even with deeply nested child calls; "
            f"target={target}"
        )


class TestOvershootClearsOnReturn:
    """When fire_start detects an overshoot, clear() removes ALL callbacks
    including on_return.  This is a separate design issue from the fire_exact
    bug above — overshoot should not destroy unrelated callback slots."""

    @requires_312
    def test_overshoot_does_not_clear_on_return(self, cc):
        def child():
            pass

        captured = []

        def nonleaf_capture():
            captured.append(cc.current())
            child()
            child()

        cc.reset()
        nonleaf_capture()
        target = captured[0]

        on_start_fired = []
        on_return_fired = []
        on_overshoot_fired = []

        cc.reset()
        tc = cc()
        tc.add_watch(
            target,
            on_start=cc.disable_for(lambda: on_start_fired.append(True)),
            on_return=cc.disable_for(lambda: on_return_fired.append(True)),
            on_overshoot=cc.disable_for(lambda: on_overshoot_fired.append(True)),
        )

        def nonleaf_replay():
            child()
            child()

        nonleaf_replay()

        assert on_start_fired == [True], (
            f"on_start should fire at function entry; target={target}"
        )
        assert on_overshoot_fired == [], (
            "on_overshoot should not fire — the target was reached, not missed"
        )
        assert on_return_fired == [True], (
            f"on_return should fire when the function returns; target={target}"
        )
