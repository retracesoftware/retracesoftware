"""Tests for control-protocol cursor navigation.

Replay cursors are retrace-python coordinate tuples now.  These tests avoid
the retired utils.CallCounter API and verify that control messages and
breakpoint snapshots use the current cursor shape.
"""

import _thread
import textwrap
import threading
from collections import deque

import pytest
import retrace

from retracesoftware.control_runtime import Controller, StopAtCursor, control_event_loop


class MockControlSocket:
    def __init__(self, requests):
        self._requests = deque(requests)
        self.responses = []

    def read_request(self):
        if not self._requests:
            return None
        return self._requests.popleft()

    def write_response(self, payload):
        self.responses.append(payload)

    def close(self):
        pass

    def breakpoint_cursor(self):
        for response in self.responses:
            if response.get("kind") == "event" and response.get("event") == "breakpoint_hit":
                return response["payload"]["cursor"]
        return None


TARGET_SOURCE = textwrap.dedent(
    """\
    def setup():
        pass

    def foo():
        a = 'bar'
        return a

    setup()
    x = 10
    y = 20
    z = x + y
    foo()
    result = z
"""
)


def _breakpoint_request(path, line, max_hits=1):
    return {
        "id": "bp-1",
        "command": "hit_breakpoints",
        "params": {
            "breakpoint": {"file": path, "line": line},
            "max_hits": max_hits,
        },
    }


def _run_to_cursor_request(cursor):
    return {
        "id": "cursor-1",
        "command": "run_to_cursor",
        "params": {"cursor": cursor},
    }


def _busy_loop(iterations=1000):
    value = 0
    for index in range(iterations):
        value += index
        value ^= index
        value += 1
    return value


def _stop_reasons(responses):
    return [
        response["payload"]["reason"]
        for response in responses
        if response.get("kind") == "stop"
    ]


def _run_worker_to_cursor(delta):
    ready = threading.Event()
    go = threading.Event()
    errors = []
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
    cursor = {
        "thread_id": target_id,
        "coordinates": list((*base[:-1], base[-1] + delta)),
    }
    socket = MockControlSocket([_run_to_cursor_request(cursor)])
    Controller(control_socket=socket)

    try:
        go.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        return socket.responses
    finally:
        retrace.call_at(None)


def test_run_to_cursor_overshoot_reports_stop_reason():
    socket = MockControlSocket([
        _run_to_cursor_request({"thread_id": 1, "coordinates": [999, 0]})
    ])
    loop = control_event_loop(lambda _: None, socket, get_message_index=lambda: 42)

    intent = next(loop)
    assert isinstance(intent, StopAtCursor)

    with pytest.raises(StopIteration):
        loop.send("overshoot")

    assert socket.responses == [
        {
            "kind": "stop",
            "payload": {
                "reason": "overshoot",
                "message_index": 42,
                "cursor": {},
                "thread_cursors": {},
            },
        }
    ]


def test_controller_run_to_cursor_hits_worker_coordinate():
    last_responses = None
    for delta in range(1, 64):
        responses = _run_worker_to_cursor(delta)
        last_responses = responses
        if _stop_reasons(responses) == ["cursor"]:
            break
    else:
        pytest.fail(f"could not find reachable run_to_cursor coordinate: {last_responses}")

    stop = [response for response in responses if response.get("kind") == "stop"][0]
    cursor = stop["payload"]["cursor"]
    assert "coordinates" in cursor
    assert "function_counts" not in cursor
    assert cursor["thread_id"] != 0


@pytest.fixture
def target_code(tmp_path):
    path = tmp_path / "target.py"
    path.write_text(TARGET_SOURCE)
    return compile(TARGET_SOURCE, str(path), "exec"), str(path)


def test_breakpoint_inside_function_returns_coordinate_cursor(target_code):
    code, path = target_code
    socket = MockControlSocket([_breakpoint_request(path, 5)])

    Controller(control_socket=socket)
    exec(code, {"__name__": "__target__", "__file__": path})

    cursor = socket.breakpoint_cursor()
    assert cursor is not None, socket.responses
    assert cursor["lineno"] == 5
    assert cursor["thread_id"] != 0
    assert len(cursor["coordinates"]) >= 4
    assert "function_counts" not in cursor


def test_breakpoint_on_return_line_returns_coordinate_cursor(target_code):
    code, path = target_code
    socket = MockControlSocket([_breakpoint_request(path, 6)])

    Controller(control_socket=socket)
    exec(code, {"__name__": "__target__", "__file__": path})

    cursor = socket.breakpoint_cursor()
    assert cursor is not None, socket.responses
    assert cursor["lineno"] == 6
    assert cursor["thread_id"] != 0
    assert len(cursor["coordinates"]) >= 4
    assert "function_counts" not in cursor


def test_module_level_breakpoint_scan_only(target_code):
    code, path = target_code
    socket = MockControlSocket([_breakpoint_request(path, 9)])

    Controller(control_socket=socket)
    exec(code, {"__name__": "__target__", "__file__": path})

    cursor = socket.breakpoint_cursor()
    assert cursor is not None, socket.responses
    assert cursor["lineno"] == 9
    assert cursor["thread_id"] != 0
    assert len(cursor["coordinates"]) >= 2
    assert "function_counts" not in cursor
