"""End-to-end test: record unframed, replay with --stdio, validate breakpoint hit."""
import json
import os
import sys
import shutil
import tempfile
import subprocess

import pytest

PYTHON = sys.executable
TIMEOUT = 30
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")

needs_monitoring = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="sys.monitoring requires Python 3.12+",
)


@pytest.fixture
def tmpdir():
    d = tempfile.mkdtemp(prefix="retrace_stdio_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def record_raw(script_path, trace_path):
    """Record a script with unframed binary output (no PID framing)."""
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", trace_path,
        "--format", "unframed_binary",
        "--", script_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    assert result.returncode == 0, f"Record failed:\n{result.stderr}"
    assert os.path.isfile(trace_path), "Trace file not created"
    return result


def replay_stdio(trace_path, commands):
    """Replay an unframed trace with --stdio, sending JSON commands and collecting responses.

    Returns a list of parsed JSON response dicts.
    """
    stdin_data = "\n".join(json.dumps(c) for c in commands) + "\n"

    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", trace_path,
        "--stdio",
    ]
    result = subprocess.run(
        cmd, input=stdin_data,
        capture_output=True, text=True, timeout=TIMEOUT,
    )

    responses = []
    for line in result.stdout.strip().splitlines():
        if line.strip():
            responses.append(json.loads(line))

    return responses, result


def test_hello_close(tmpdir):
    """Simplest test: hello handshake then close."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    commands = [
        {"command": "hello"},
        {"command": "close"},
    ]
    responses, result = replay_stdio(trace, commands)

    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"
    assert len(responses) == 2

    hello_resp = responses[0]
    assert hello_resp["ok"] is True
    assert hello_resp["result"]["protocol"] == "control"

    close_resp = responses[1]
    assert close_resp["ok"] is True
    assert close_resp["result"]["closed"] is True


@needs_monitoring
def test_breakpoint_hit(tmpdir):
    """Record, replay, set a breakpoint, and validate it fires."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    bp_file = os.path.realpath(script)
    bp_line = 6  # "result = a + b" inside add()

    commands = [
        {"command": "hello"},
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": bp_line},
            "max_hits": 1,
        }},
        # After first hit, search for a nonexistent line -- replay runs
        # to completion and we should get an EOF stop.
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": 999},
        }},
        {"command": "close"},
    ]
    responses, result = replay_stdio(trace, commands)

    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"

    # hello response
    assert responses[0]["ok"] is True

    # breakpoint hit event
    bp_event = responses[1]
    assert bp_event["kind"] == "event"
    assert bp_event["event"] == "breakpoint_hit"
    cursor = bp_event["payload"]["cursor"]
    assert "thread_id" in cursor
    assert "function_counts" in cursor

    # hit_breakpoints OK (max_hits=1 reached)
    bp_ok = responses[2]
    assert bp_ok["ok"] is True
    assert bp_ok["result"]["hits"] == 1

    # EOF stop from the second hit_breakpoints (line 999 never hit)
    eof_stop = responses[3]
    assert eof_stop["kind"] == "stop"
    assert eof_stop["payload"]["reason"] == "eof"

    # close response
    close_resp = responses[4]
    assert close_resp["ok"] is True
    assert close_resp["result"]["closed"] is True


@needs_monitoring
def test_breakpoint_multiple_hits(tmpdir):
    """Breakpoint on add() line fires twice (called twice in target script)."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    bp_file = os.path.realpath(script)
    bp_line = 6  # "result = a + b"

    commands = [
        {"command": "hello"},
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": bp_line},
            "max_hits": 2,
        }},
        {"command": "close"},
    ]
    responses, result = replay_stdio(trace, commands)

    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"

    # hello
    assert responses[0]["ok"] is True

    # two breakpoint hit events
    assert responses[1]["kind"] == "event"
    assert responses[1]["event"] == "breakpoint_hit"
    assert responses[2]["kind"] == "event"
    assert responses[2]["event"] == "breakpoint_hit"

    # The two hits should have different cursors (different call counters)
    cursor1 = responses[1]["payload"]["cursor"]["function_counts"]
    cursor2 = responses[2]["payload"]["cursor"]["function_counts"]
    assert cursor1 != cursor2

    # OK response
    assert responses[3]["ok"] is True
    assert responses[3]["result"]["hits"] == 2

    # close
    assert responses[4]["ok"] is True


@needs_monitoring
def test_set_backstop(tmpdir):
    """Set a backstop before hitting breakpoints -- should stop at backstop."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    bp_file = os.path.realpath(script)

    commands = [
        {"command": "hello"},
        {"command": "set_backstop", "params": {"message_index": 1}},
        # Use a line that doesn't exist so the breakpoint never fires;
        # the backstop should stop execution instead.
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": 999},
        }},
        {"command": "close"},
    ]
    responses, result = replay_stdio(trace, commands)

    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"

    # hello
    assert responses[0]["ok"] is True
    # set_backstop OK
    assert responses[1]["ok"] is True

    # Backstop fires (breakpoint on line 999 never matches)
    stop = responses[2]
    assert stop["kind"] == "stop"
    assert stop["payload"]["reason"] == "backstop"
