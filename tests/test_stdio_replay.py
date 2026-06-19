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


def test_breakpoint_cursor_stack_and_locals(tmpdir):
    """Scan a breakpoint, replay to its cursor, then inspect stack and locals."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    bp_file = os.path.realpath(script)
    bp_line = 6  # "result = a + b"

    scan_commands = [
        {"command": "hello"},
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": bp_line},
            "max_hits": 1,
        }},
    ]
    scan_responses, scan_result = replay_stdio(trace, scan_commands)
    assert scan_result.returncode == 0, f"Replay failed:\n{scan_result.stderr}"

    bp_event = next(
        r for r in scan_responses
        if r.get("kind") == "event" and r.get("event") == "breakpoint_hit"
    )
    hit_cursor = bp_event["payload"]["cursor"]
    assert hit_cursor["lineno"] == bp_line

    inspect_commands = [
        {"id": "1", "command": "hello"},
        {"id": "2", "command": "run_to_cursor", "params": {"cursor": hit_cursor}},
        {"id": "3", "command": "stack"},
        {"id": "4", "command": "locals", "params": {"frame": 0}},
    ]
    inspect_responses, inspect_result = replay_stdio(trace, inspect_commands)
    assert inspect_result.returncode == 0, f"Replay failed:\n{inspect_result.stderr}"

    stop = next(r for r in inspect_responses if r.get("kind") == "stop")
    assert stop["payload"]["reason"] == "cursor"
    assert stop["payload"]["cursor"]["lineno"] == bp_line

    stack = next(r for r in inspect_responses if r.get("id") == "3")
    assert stack["ok"] is True
    frames = stack["result"]["frames"]
    assert frames[0]["filename"] == bp_file
    assert frames[0]["line"] == bp_line
    assert frames[0]["function"] == "add"

    locals_response = next(r for r in inspect_responses if r.get("id") == "4")
    assert locals_response["ok"] is True
    local_names = {item["name"] for item in locals_response["result"]["variables"]}
    assert {"a", "b"} <= local_names


def test_run_to_cursor_then_next_instruction(tmpdir):
    """Run to a materialized cursor, then advance one bytecode instruction."""
    script = os.path.join(SCRIPTS_DIR, "breakpoint_target.py")
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    bp_file = os.path.realpath(script)
    bp_line = 6  # "result = a + b"

    scan_responses, scan_result = replay_stdio(trace, [
        {"command": "hello"},
        {"command": "hit_breakpoints", "params": {
            "breakpoint": {"file": bp_file, "line": bp_line},
            "max_hits": 1,
        }},
    ])
    assert scan_result.returncode == 0, f"Replay failed:\n{scan_result.stderr}"

    bp_event = next(
        r for r in scan_responses
        if r.get("kind") == "event" and r.get("event") == "breakpoint_hit"
    )
    hit_cursor = bp_event["payload"]["cursor"]

    responses, result = replay_stdio(trace, [
        {"id": "1", "command": "hello"},
        {"id": "2", "command": "run_to_cursor", "params": {"cursor": hit_cursor}},
        {"id": "3", "command": "next_instruction"},
        {"id": "4", "command": "stack"},
    ])
    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"

    stops = [r for r in responses if r.get("kind") == "stop"]
    assert [s["payload"]["reason"] for s in stops] == ["cursor", "instruction"]
    cursor_stop = stops[0]["payload"]["cursor"]
    instruction_stop = stops[1]["payload"]["cursor"]
    assert cursor_stop["lineno"] == bp_line
    assert instruction_stop["f_lasti"] != cursor_stop["f_lasti"]

    stack = next(r for r in responses if r.get("id") == "4")
    assert stack["ok"] is True
    assert stack["result"]["frames"][0]["filename"] == bp_file


def test_thread_breakpoint_hits_with_stdio_replay(tmpdir):
    """Source breakpoints should fire in worker threads during stdio replay."""
    script = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "examples", "target_threads.py"))
    trace = os.path.join(tmpdir, "trace.retrace")

    record_raw(script, trace)

    responses, result = replay_stdio(trace, [
        {"id": "1", "command": "hello"},
        {"id": "2", "command": "hit_breakpoints", "params": {
            "breakpoint": {"file": script, "line": 8},
            "max_hits": 6,
        }},
    ])
    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"

    hits = [
        r["payload"]["cursor"]
        for r in responses
        if r.get("kind") == "event" and r.get("event") == "breakpoint_hit"
    ]
    assert len(hits) == 6
    assert all(hit["lineno"] == 8 for hit in hits)
