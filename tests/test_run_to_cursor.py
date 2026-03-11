"""End-to-end test of the Python replay control protocol:
hit_breakpoints → capture cursor → run_to_return + next_instruction → verify position.

This replicates the Go-side RunToCursor logic purely in Python, testing the
full pipeline from breakpoint scanning through to cursor navigation.

Controller initialization is wrapped in call_counter_disable_for and exec()
is called directly from the test (not via a helper function) so the only
frames tracked by the call counter are the target code frames.
"""
import sys
import textwrap
from collections import deque

import pytest

import retracesoftware.utils as utils
from retracesoftware.control_runtime import Controller

requires_312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="Breakpoints require sys.monitoring (Python 3.12+)",
)


def _run_to_cursor_prev(function_counts):
    """Compute the function_counts to pass to run_to_return in order to
    reach `function_counts` via a subsequent next_instruction loop.

    The result is padded to the same depth as the target so that
    check_exit_slot (which requires cursor_stack.size() == target.size())
    matches the correct sibling call, not the parent.

    For target [8,3,2] returns [8,3,1].
    For target [8,3,0] returns [8,2,0] (go up one level, pad with 0).
    For target [0] returns None (already at start).
    """
    for i in range(len(function_counts) - 1, -1, -1):
        if function_counts[i] > 0:
            prev = list(function_counts[:i + 1])
            prev[i] -= 1
            while len(prev) < len(function_counts):
                prev.append(0)
            return prev
    return None


class ScanSocket:
    """Mock socket that sends hit_breakpoints and collects responses."""

    def __init__(self, breakpoint_spec):
        self._requests = deque([{
            "request_id": "bp-1",
            "command": "hit_breakpoints",
            "params": {"breakpoint": breakpoint_spec, "max_hits": 1},
        }])
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
        for r in self.responses:
            if r.get("kind") == "event" and r.get("event") == "breakpoint_hit":
                return r["payload"]["cursor"]
        return None


class NavigateSocket:
    """Mock socket that drives fork + run_to_return + next_instruction to
    reach a target cursor, mirroring Go's RunToCursor logic.

    Sends hello → fork → run_to_return → next_instruction, exercising the
    same control protocol flow that the Go replay uses."""

    MAX_STEPS = 2000

    def __init__(self, target_cursor):
        self._requests = deque()
        self.responses = []
        self._target = target_cursor
        self._done = False
        self._step_count = 0

        self._requests.append({
            "request_id": "hello-1",
            "command": "hello",
            "params": {},
        })
        self._requests.append({
            "request_id": "fork-1",
            "command": "fork",
            "params": {"fork_id": "test"},
        })

        fc = target_cursor["function_counts"]
        tid = target_cursor["thread_id"]
        prev = _run_to_cursor_prev(fc)

        if prev:
            self._requests.append({
                "request_id": "rtr-1",
                "command": "run_to_return",
                "params": {"thread_id": tid, "function_counts": prev},
            })
        self._requests.append({
            "request_id": "ni-0",
            "command": "next_instruction",
            "params": {},
        })

    def close(self):
        pass

    @property
    def reached_target(self):
        return self._done

    def read_request(self):
        if self._done or not self._requests:
            return None
        return self._requests.popleft()

    def write_response(self, payload):
        self.responses.append(payload)
        if payload.get("kind") != "stop":
            return
        cursor = payload["payload"]["cursor"]
        reason = payload["payload"]["reason"]
        if reason not in ("return", "instruction"):
            return

        target = self._target
        fc_match = cursor.get("function_counts") == target["function_counts"]
        flasti_match = (
            target.get("f_lasti") is None
            or cursor.get("f_lasti") == target.get("f_lasti")
        )
        if fc_match and flasti_match:
            self._done = True
            return

        self._step_count += 1
        if self._step_count > self.MAX_STEPS:
            self._done = True
            return

        self._requests.append({
            "request_id": f"ni-{self._step_count}",
            "command": "next_instruction",
            "params": {},
        })


TARGET_SOURCE = textwrap.dedent("""\
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
""")


def _init_controller(sock):
    Controller(control_socket=sock)


@pytest.fixture
def target_code(tmp_path):
    """Returns (code_object, file_path) for the target script."""
    p = tmp_path / "target.py"
    p.write_text(TARGET_SOURCE)
    path = str(p)
    code = compile(TARGET_SOURCE, path, "exec")
    return code, path


@pytest.fixture(autouse=True)
def _clean_call_counter():
    yield
    try:
        utils.uninstall_call_counter()
    except Exception:
        pass


@requires_312
class TestRunToCursor:
    def test_breakpoint_inside_function(self, target_code):
        """Set breakpoint inside foo() (line 5: a = 'bar').
        Phase 1 scans for the cursor, phase 2 navigates to it using
        run_to_return + next_instruction."""
        code, path = target_code
        cc = utils.CallCounter()
        silent = utils.call_counter_disable_for(_init_controller)

        # --- Phase 1: scan ---
        scan = ScanSocket({"file": path, "line": 5})
        with cc:
            silent(scan)
            exec(code, {"__name__": "__target__", "__file__": path})

        bp_cursor = scan.breakpoint_cursor()
        assert bp_cursor is not None, (
            f"Breakpoint at line 5 was not hit; responses={scan.responses}"
        )
        assert len(bp_cursor["function_counts"]) >= 2, (
            f"Breakpoint inside foo() should have depth >= 2; "
            f"got fc={bp_cursor['function_counts']}"
        )

        # --- Phase 2: navigate ---
        nav = NavigateSocket(bp_cursor)
        with cc:
            silent(nav)
            exec(code, {"__name__": "__target__", "__file__": path})

        assert nav.reached_target, (
            f"Did not reach target cursor {bp_cursor}; "
            f"responses={nav.responses}"
        )

        stop_msgs = [r for r in nav.responses if r.get("kind") == "stop"]
        final_cursor = stop_msgs[-1]["payload"]["cursor"]
        assert final_cursor["function_counts"] == bp_cursor["function_counts"]
        if bp_cursor.get("f_lasti") is not None:
            assert final_cursor.get("f_lasti") == bp_cursor["f_lasti"]

    def test_breakpoint_on_return_line(self, target_code):
        """Set breakpoint on the return statement inside foo (line 6),
        scan and navigate."""
        code, path = target_code
        cc = utils.CallCounter()
        silent = utils.call_counter_disable_for(_init_controller)

        # Phase 1
        scan = ScanSocket({"file": path, "line": 6})
        with cc:
            silent(scan)
            exec(code, {"__name__": "__target__", "__file__": path})

        bp_cursor = scan.breakpoint_cursor()
        assert bp_cursor is not None, (
            f"Breakpoint at line 6 was not hit; responses={scan.responses}"
        )

        # Phase 2
        nav = NavigateSocket(bp_cursor)
        with cc:
            silent(nav)
            exec(code, {"__name__": "__target__", "__file__": path})

        assert nav.reached_target, (
            f"Did not reach target cursor {bp_cursor}; "
            f"responses={nav.responses}"
        )

        stop_msgs = [r for r in nav.responses if r.get("kind") == "stop"]
        final_cursor = stop_msgs[-1]["payload"]["cursor"]
        assert final_cursor["function_counts"] == bp_cursor["function_counts"]
        if bp_cursor.get("f_lasti") is not None:
            assert final_cursor.get("f_lasti") == bp_cursor["f_lasti"]

    def test_module_level_breakpoint_scan_only(self, target_code):
        """Set breakpoint at module-level line 10 (x = 10) and verify
        the scan phase produces a valid cursor with correct position."""
        code, path = target_code
        cc = utils.CallCounter()
        silent = utils.call_counter_disable_for(_init_controller)

        scan = ScanSocket({"file": path, "line": 10})
        with cc:
            silent(scan)
            exec(code, {"__name__": "__target__", "__file__": path})

        bp_cursor = scan.breakpoint_cursor()
        assert bp_cursor is not None, (
            f"Breakpoint at line 10 was not hit; responses={scan.responses}"
        )
        assert "function_counts" in bp_cursor
        assert "thread_id" in bp_cursor
        assert bp_cursor["function_counts"][0] > 0, (
            f"Module-level breakpoint after setup() should have "
            f"function_counts[0] > 0; got {bp_cursor['function_counts']}"
        )
