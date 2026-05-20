import sys
import _thread
import threading
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


def _advance_leaf_instruction(coordinates, delta):
    return (*coordinates[:-2], coordinates[-2] + delta, 0)


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

    def attempt(delta):
        errors = []
        ready = threading.Event()
        go = threading.Event()
        idents = {}

        def worker():
            try:
                idents["target"] = _thread.get_ident()
                ready.set()
                assert go.wait(5)
                _busy_loop()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        assert ready.wait(5)

        target_id = idents["target"]
        base = tuple(retrace.coordinates(target_id))
        target = _advance_leaf_instruction(base, delta)
        monitor = None
        try:
            monitor = utils.trace_function_instructions(
                target,
                lambda code, offset: hits.append((code, offset)),
                thread_id=target_id,
            )
        except utils.TargetUnreachableError:
            go.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert not errors
            return False

        try:
            go.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert not errors
            return bool(hits)
        finally:
            monitor.close()
            retrace.call_at(None)

    for delta in range(1, 1000):
        if attempt(delta):
            break
    else:
        pytest.fail("could not find a reachable future coordinate")

    assert hits
    assert all(isinstance(code, CodeType) for code, _ in hits)
    assert all(isinstance(offset, int) for _, offset in hits)


def test_target_unreachable_error_releases_monitoring_tool_id():
    coordinates = tuple(retrace.coordinates())
    for _ in range(1000):
        if coordinates[-2] > 0:
            break
        coordinates = tuple(retrace.coordinates())
    assert coordinates[-2] > 0
    past = (*coordinates[:-2], coordinates[-2] - 1, 0)

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
