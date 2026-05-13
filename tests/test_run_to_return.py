"""Tests for the run_to_return control protocol primitive.

The current control protocol targets retrace-python coordinate cursors.  These
tests keep the old call-counter API out of the runtime path and exercise the
same public cursor shape used by replay.
"""

import _thread
import threading
from collections import deque

import pytest
import retrace

from retracesoftware.control_runtime import Controller, RunToReturn, control_event_loop


class MockControlSocket:
    """Fake ControlSocket backed by in-memory queues."""

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


def _make_run_to_return_request(cursor):
    return {
        "id": "rtr-1",
        "command": "run_to_return",
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


def _cursor_events(responses):
    return [
        response["payload"]["cursor"]
        for response in responses
        if response.get("kind") == "event" and response.get("event") == "cursor"
    ]


def _run_worker_to_return(delta, workload):
    ready = threading.Event()
    go = threading.Event()
    errors = []
    idents = {}

    def worker():
        try:
            idents["target"] = _thread.get_ident()
            ready.set()
            assert go.wait(5)
            workload()
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
    socket = MockControlSocket([_make_run_to_return_request(cursor)])
    Controller(control_socket=socket)

    try:
        go.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        return socket.responses
    finally:
        retrace.call_at(None)


def _drive_until_return(workload):
    last_responses = None
    for delta in range(1, 64):
        responses = _run_worker_to_return(delta, workload)
        last_responses = responses
        if _stop_reasons(responses) == ["return"]:
            return responses
    pytest.fail(f"could not find reachable run_to_return coordinate: {last_responses}")


def test_event_loop_run_to_return_uses_coordinate_cursor():
    cursor = {"thread_id": 123, "coordinates": [7, 11]}
    socket = MockControlSocket([_make_run_to_return_request(cursor)])
    loop = control_event_loop(lambda _: None, socket, get_message_index=lambda: 42)

    intent = next(loop)
    assert isinstance(intent, RunToReturn)
    assert intent.cursor == cursor

    hit_cursor = {"thread_id": 123, "coordinates": [7, 11], "f_lasti": 4}
    assert isinstance(loop.send(hit_cursor), RunToReturn)

    with pytest.raises(StopIteration):
        loop.send("return")

    assert socket.responses == [
        {
            "id": "rtr-1",
            "kind": "event",
            "event": "cursor",
            "payload": {"cursor": hit_cursor, "message_index": 42},
        },
        {
            "kind": "stop",
            "payload": {
                "reason": "return",
                "message_index": 42,
                "cursor": hit_cursor,
                "thread_cursors": {},
            },
        },
    ]


def test_controller_run_to_return_fires_for_worker_coordinate():
    responses = _drive_until_return(lambda: _busy_loop())

    events = _cursor_events(responses)
    assert len(events) == 1
    assert "coordinates" in events[0]
    assert "function_counts" not in events[0]
    assert _stop_reasons(responses) == ["return"]


def test_controller_run_to_return_survives_child_calls_before_hit():
    def child():
        _busy_loop(20)

    def workload():
        child()
        _busy_loop()
        child()

    responses = _drive_until_return(workload)

    events = _cursor_events(responses)
    assert len(events) == 1
    assert events[0]["thread_id"] != 0
    assert len(events[0]["coordinates"]) >= 2
    assert _stop_reasons(responses) == ["return"]
