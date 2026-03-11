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


@requires_311
class TestRunToReturn:
    def test_basic_run_to_return_fires(self):
        """Controller._install_run_to_return should fire on_return and
        send a stop message with a valid cursor."""
        tid = _thread.get_ident()
        cc = utils.CallCounter()

        def child():
            pass

        def target_fn(probe):
            probe()
            child()
            child()

        entries = []

        def capture_target():
            entries.append(utils.current_call_counts())

        def driver(probe):
            target_fn(probe)

        with cc:
            driver(cc.disable_for(capture_target))
        assert len(entries) == 1
        target = entries[0]

        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        with cc:
            cc.disable_for(lambda: Controller(control_socket=sock))()
            driver(cc.disable_for(lambda: None))

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
        tid = _thread.get_ident()
        cc = utils.CallCounter()

        def deep_child():
            pass

        def mid_child():
            deep_child()

        entries = []

        def capture():
            entries.append(utils.current_call_counts())

        def target_fn(probe):
            probe()
            mid_child()
            deep_child()
            mid_child()

        with cc:
            target_fn(cc.disable_for(capture))
        target = entries[0]

        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        with cc:
            cc.disable_for(lambda: Controller(control_socket=sock))()
            target_fn(cc.disable_for(lambda: None))

        stop_msgs = [r for r in sock.responses if r.get("kind") == "stop"]
        assert len(stop_msgs) == 1, (
            f"Expected one stop message; got {sock.responses}"
        )
        cursor = stop_msgs[0]["payload"]["cursor"]
        assert len(cursor["function_counts"]) == len(target)

    def test_run_to_return_deeply_nested(self):
        """Verify run_to_return for a function several levels deep."""
        tid = _thread.get_ident()
        cc = utils.CallCounter()

        def leaf():
            pass

        entries = []

        def capture_level3():
            entries.append(utils.current_call_counts())

        def level3(probe):
            probe()
            leaf()

        def level2(probe):
            level3(probe)

        def level1(probe):
            level2(probe)

        def driver(probe):
            level1(probe)

        with cc:
            driver(cc.disable_for(capture_level3))
        target = entries[0]
        assert len(target) >= 3

        sock = MockControlSocket([
            _make_run_to_return_request(tid, target),
        ])

        with cc:
            cc.disable_for(lambda: Controller(control_socket=sock))()
            driver(cc.disable_for(lambda: None))

        stop_msgs = [r for r in sock.responses if r.get("kind") == "stop"]
        assert len(stop_msgs) == 1, (
            f"Expected one stop message; got {sock.responses}"
        )
        cursor = stop_msgs[0]["payload"]["cursor"]
        assert len(cursor["function_counts"]) == len(target)
