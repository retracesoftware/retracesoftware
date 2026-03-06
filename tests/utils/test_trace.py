import sys
import threading
from types import CodeType

import pytest

_utils = pytest.importorskip("retracesoftware.utils")
from retracesoftware.utils.trace import _classify_position  # noqa: E402

requires_312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="INSTRUCTION monitoring requires Python 3.12+",
)


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
# _classify_position — pure logic, no C extension
# ============================================================================


class TestClassifyPosition:
    def test_exact_match(self):
        assert _classify_position((3, 2), (3, 2)) == "exact"

    def test_exact_empty(self):
        assert _classify_position((), ()) == "exact"

    def test_behind_first_element(self):
        assert _classify_position((3, 2), (3, 5)) == "behind"

    def test_behind_second_element(self):
        assert _classify_position((3, 2), (4, 0)) == "behind"

    def test_ahead_first_element(self):
        assert _classify_position((3, 5), (3, 2)) == "ahead"

    def test_ahead_second_element(self):
        assert _classify_position((4, 0), (3, 2)) == "ahead"

    def test_ancestor_prefix(self):
        assert _classify_position((3,), (3, 2, 1)) == "ancestor"

    def test_ancestor_single_level(self):
        assert _classify_position((3, 2), (3, 2, 1)) == "ancestor"

    def test_ahead_deeper_target(self):
        assert _classify_position((3, 2, 1), (3,)) == "ahead"

    def test_ahead_much_deeper(self):
        assert _classify_position((3, 2, 1, 5), (3, 2)) == "ahead"

    def test_behind_diverges_early(self):
        assert _classify_position((1, 9, 9), (2, 0, 0)) == "behind"

    def test_ahead_diverges_early(self):
        assert _classify_position((2, 0, 0), (1, 9, 9)) == "ahead"


# ============================================================================
# TargetUnreachableError — "behind" case
# ============================================================================


@requires_312
class TestBehindRaisesError:
    def test_raises_when_target_already_passed(self):
        _utils.install_call_counter()

        results = []

        def mark():
            results.append(_utils.current_call_counts())

        mark()
        mark()
        mark()

        target = results[0]

        with pytest.raises(_utils.TargetUnreachableError):
            _utils.trace_function_instructions(
                threading.get_ident(), target, lambda code, offset: None
            )


# ============================================================================
# "ahead" case — target not yet entered
# ============================================================================


@requires_312
class TestAheadCase:
    def test_callback_fires_for_target_instructions(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        hits = []

        def target_fn():
            x = 1  # noqa: F841
            y = 2  # noqa: F841
            return x + y

        def gather_target_counters():
            return _utils.current_call_counts()

        first_counters = gather_target_counters()

        _utils.call_counter_reset()

        target_counters = first_counters

        monitor = _utils.trace_function_instructions(
            threading.get_ident(),
            target_counters,
            lambda code, offset: hits.append((code, offset)),
        )

        gather_target_counters()
        target_fn()

        assert len(hits) > 0
        monitor.close()

    def test_callback_receives_code_and_int_offset(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        hits = []

        def target_fn():
            return 42

        def probe():
            return _utils.current_call_counts()

        counters = probe()

        _utils.call_counter_reset()

        monitor = _utils.trace_function_instructions(
            threading.get_ident(),
            counters,
            lambda code, offset: hits.append((code, offset)),
        )

        probe()
        target_fn()

        assert len(hits) > 0
        code_obj, offset = hits[0]
        assert isinstance(code_obj, CodeType)
        assert isinstance(offset, int)
        monitor.close()


# ============================================================================
# "ancestor" case — currently deeper than target
# ============================================================================


@requires_312
class TestAncestorCase:
    def test_activates_when_returning_to_target(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        hits = []
        monitor_holder = [None]

        def outer():
            x = 1  # noqa: F841
            inner()
            y = 2  # noqa: F841
            return x + y

        def inner():
            outer_counters = _utils.current_call_counts()[:-1]
            monitor_holder[0] = _utils.trace_function_instructions(
                threading.get_ident(),
                outer_counters,
                lambda code, offset: hits.append(offset),
                target_frame=sys._getframe(1),
            )

        outer()

        assert len(hits) > 0, "Should have received instruction callbacks after returning to outer"
        if monitor_holder[0] is not None:
            monitor_holder[0].close()


# ============================================================================
# Auto-teardown on function exit
# ============================================================================


@requires_312
class TestAutoTeardown:
    def test_monitor_closes_after_target_returns(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        def target_fn():
            return 42

        def probe():
            return _utils.current_call_counts()

        counters = probe()

        _utils.call_counter_reset()

        monitor = _utils.trace_function_instructions(
            threading.get_ident(),
            counters,
            lambda code, offset: None,
        )

        assert not monitor._closed

        probe()
        target_fn()

        assert monitor._closed, "Monitor should auto-close after target function returns"


# ============================================================================
# Manual .close()
# ============================================================================


@requires_312
class TestManualClose:
    def test_close_stops_callbacks(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        hits = []

        def probe():
            return _utils.current_call_counts()

        counters = probe()

        _utils.call_counter_reset()

        monitor = _utils.trace_function_instructions(
            threading.get_ident(),
            counters,
            lambda code, offset: hits.append(offset),
        )

        monitor.close()

        assert monitor._closed

        probe()

        def after_close():
            x = 1  # noqa: F841
            y = 2  # noqa: F841
            return x + y

        after_close()

        assert len(hits) == 0, "No callbacks should fire after manual close"

    def test_double_close_is_safe(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        def probe():
            return _utils.current_call_counts()

        counters = probe()

        _utils.call_counter_reset()

        monitor = _utils.trace_function_instructions(
            threading.get_ident(),
            counters,
            lambda code, offset: None,
        )

        monitor.close()
        monitor.close()
        assert monitor._closed


# ============================================================================
# "exact" case — requires target_frame
# ============================================================================


@requires_312
class TestExactCase:
    def test_raises_without_target_frame(self):
        _utils.install_call_counter()

        counters = _utils.current_call_counts()

        with pytest.raises(ValueError, match="target_frame"):
            _utils.trace_function_instructions(
                threading.get_ident(),
                counters,
                lambda code, offset: None,
            )

    def test_works_with_target_frame(self):
        _utils.install_call_counter()
        _utils.call_counter_reset()

        hits = []
        monitor_holder = [None]

        def target_fn():
            frame = sys._getframe(0)
            counters = _utils.current_call_counts()
            monitor_holder[0] = _utils.trace_function_instructions(
                threading.get_ident(),
                counters,
                lambda code, offset: hits.append(offset),
                target_frame=frame,
            )
            x = 1  # noqa: F841
            y = 2  # noqa: F841
            return x + y

        target_fn()

        assert len(hits) > 0, "Should fire callbacks for remaining instructions"
        if monitor_holder[0] is not None:
            monitor_holder[0].close()
