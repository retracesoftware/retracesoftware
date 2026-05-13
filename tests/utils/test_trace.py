import sys
from types import CodeType

import pytest

retrace = pytest.importorskip("retrace")
utils = pytest.importorskip("retracesoftware.utils")


def _busy_loop():
    value = 0
    for index in range(1000):
        value += index
        value ^= index
        value += 1
    return value


def test_trace_current_frame_with_explicit_target_frame():
    hits = []
    completed = []
    holder = {}

    def traced():
        frame = sys._getframe()
        monitor = utils.trace_function_instructions(
            retrace.coordinates(),
            lambda code, offset: hits.append((code, offset)),
            target_frame=frame,
            on_complete=lambda: completed.append(True),
        )
        holder["monitor"] = monitor
        x = 1
        y = 2
        return x + y

    assert traced() == 3

    assert completed == [True]
    assert holder["monitor"]._closed
    assert hits
    assert all(isinstance(code, CodeType) for code, _ in hits)
    assert all(isinstance(offset, int) for _, offset in hits)
    assert any(code is traced.__code__ for code, _ in hits)


def test_trace_future_coordinate_uses_retrace_call_at():
    hits = []
    monitor = None

    for delta in range(1, 1000):
        base = tuple(retrace.coordinates())
        target = (*base[:-1], base[-1] + delta)
        try:
            monitor = utils.trace_function_instructions(
                target,
                lambda code, offset: hits.append((code, offset)),
            )
        except utils.TargetUnreachableError:
            continue
        try:
            _busy_loop()
            if hits:
                break
        finally:
            monitor.close()
            retrace.call_at(None)
    else:
        pytest.fail("could not find a reachable future coordinate")

    assert hits
    assert all(isinstance(code, CodeType) for code, _ in hits)
    assert all(isinstance(offset, int) for _, offset in hits)


def test_target_unreachable_error_releases_monitoring_tool_id():
    coordinates = tuple(retrace.coordinates())
    for _ in range(1000):
        if coordinates[-1] > 0:
            break
        coordinates = tuple(retrace.coordinates())
    assert coordinates[-1] > 0
    past = (*coordinates[:-1], coordinates[-1] - 1)

    for _ in range(8):
        with pytest.raises(utils.TargetUnreachableError):
            utils.trace_function_instructions(past, lambda code, offset: None)

    hits = []

    def traced():
        monitor = utils.trace_function_instructions(
            retrace.coordinates(),
            lambda code, offset: hits.append(offset),
            target_frame=sys._getframe(),
        )
        holder.append(monitor)
        return 42

    holder = []
    assert traced() == 42
    assert holder[0]._closed
    assert hits


def test_monitor_close_is_idempotent():
    hits = []

    def traced():
        monitor = utils.trace_function_instructions(
            retrace.coordinates(),
            lambda code, offset: hits.append(offset),
            target_frame=sys._getframe(),
        )
        monitor.close()
        monitor.close()
        x = 1
        y = 2
        return x + y, monitor

    result, monitor = traced()

    assert result == 3
    assert monitor._closed


def test_on_complete_is_not_called_by_manual_close():
    completed = []

    def traced():
        monitor = utils.trace_function_instructions(
            retrace.coordinates(),
            lambda code, offset: None,
            target_frame=sys._getframe(),
            on_complete=lambda: completed.append(True),
        )
        monitor.close()
        return monitor

    monitor = traced()

    assert monitor._closed
    assert completed == []
