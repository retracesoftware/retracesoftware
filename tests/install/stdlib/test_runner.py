"""Test the Runner API itself.

Verifies record/replay/run semantics, divergence detection, and
error handling.
"""
import socket
import sys

import pytest

from tests.runner import Runner, retrace_test


@retrace_test
def test_run_returns_value():
    """run() returns the recorded return value."""
    result = socket.gethostname()
    assert isinstance(result, str)
    assert len(result) > 0
    return result


def test_record_then_replay():
    """Separate record() and replay() calls work correctly."""
    runner = Runner()

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None

    result = runner.replay(recording, do_hostname)
    assert result == recording.result


def test_recording_captures_result():
    """Recording captures the result of the function call."""
    runner = Runner()

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None


def test_runner_accepts_core_matrix_preset():
    """Runner should accept the named core matrix preset."""
    runner = Runner(matrix="core")

    def do_hostname():
        return socket.gethostname()

    result = runner.run(do_hostname)
    assert isinstance(result, str)


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="sys.monitoring requires Python 3.12+",
)
def test_record_supports_monitor_level_1():
    """record() should still work when monitoring is enabled."""
    runner = Runner(monitor=1)

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.error is None
    assert "MONITOR" in recording.tape
