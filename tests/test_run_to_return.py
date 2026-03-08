"""Tests for the run_to_return control protocol primitive.

Verifies that Controller._install_run_to_return correctly arms
utils.watch(on_return=...) and that the resulting cursor_snapshot
is sent back through the control event loop.
"""
import sys
import _thread
from collections import deque

import pytest

import retracesoftware.utils as utils
from retracesoftware.control_runtime import Controller

requires_311 = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="CallCounter hooks require Python 3.11+",
)


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


def _make_run_to_return_request(thread_id, function_counts):
    return {
        "request_id": "rtr-1",
        "command": "run_to_return",
        "params": {
            "thread_id": thread_id,
            "function_counts": list(function_counts),
        },
    }


@pytest.fixture(autouse=True)
def _clean_call_counter():
    yield
    try:
        utils.uninstall_call_counter()
    except Exception:
        pass
    utils.call_counter_reset()


@requires_311
class TestRunToReturn:
    def test_basic_run_to_return_fires(self):
        """Controller._install_run_to_return should fire on_return and
        send a stop message with a valid cursor."""
        utils.install_call_counter()
        utils.call_counter_reset()

        tid = _thread.get_ident()

        def child():
            pass

        def target_fn():
            child()
            child()

        def driver():
            target_fn()

        # First pass: record entry call counts for target_fn
        entries = []
        def capture_target():
            entries.append(utils.current_call_counts())
            child()
            child()

        def capture_driver():
            capture_target()

        capture_driver()
        assert len(entries) == 1
        target = entries[0]

        # Second pass: feed run_to_return command into Controller
        utils.call_counter_reset()
        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        controller = Controller(control_socket=sock)

        # Now run the same function structure; on_return should fire
        capture_driver()

        # Verify stop message was written
        stop_msgs = [r for r in sock.responses if r.get("kind") == "stop"]
        assert len(stop_msgs) == 1, (
            f"Expected exactly one stop message; got responses={sock.responses}"
        )
        payload = stop_msgs[0]["payload"]
        assert payload["reason"] == "return"
        cursor = payload["cursor"]
        assert "thread_id" in cursor
        assert "function_counts" in cursor
        assert cursor["thread_id"] == tid
        fc = cursor["function_counts"]
        assert len(fc) == len(target), (
            f"on_return fires pre-pop, cursor depth should match target: "
            f"fc={fc} target={target}"
        )

    def test_run_to_return_with_subcalls(self):
        """on_return must fire even when the target function calls subcalls
        (which change the last element of function_counts)."""
        utils.install_call_counter()
        utils.call_counter_reset()

        tid = _thread.get_ident()

        def deep_child():
            pass

        def mid_child():
            deep_child()

        # First pass
        entries = []
        def capture():
            entries.append(utils.current_call_counts())
            mid_child()
            deep_child()
            mid_child()

        def driver():
            capture()

        driver()
        target = entries[0]

        # Second pass
        utils.call_counter_reset()
        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        controller = Controller(control_socket=sock)
        driver()

        stop_msgs = [r for r in sock.responses if r.get("kind") == "stop"]
        assert len(stop_msgs) == 1, (
            f"Expected one stop message; got {sock.responses}"
        )
        cursor = stop_msgs[0]["payload"]["cursor"]
        assert len(cursor["function_counts"]) == len(target)

    def test_run_to_return_deeply_nested(self):
        """Verify run_to_return for a function several levels deep."""
        utils.install_call_counter()
        utils.call_counter_reset()

        tid = _thread.get_ident()

        def leaf():
            pass

        entries = []
        def capture_level3():
            entries.append(utils.current_call_counts())
            leaf()

        def level2():
            capture_level3()

        def level1():
            level2()

        def driver():
            level1()

        driver()
        target = entries[0]
        assert len(target) >= 3

        utils.call_counter_reset()
        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        controller = Controller(control_socket=sock)
        driver()

        stop_msgs = [r for r in sock.responses if r.get("kind") == "stop"]
        assert len(stop_msgs) == 1, (
            f"Expected one stop message; got {sock.responses}"
        )
        cursor = stop_msgs[0]["payload"]["cursor"]
        assert len(cursor["function_counts"]) == len(target)
