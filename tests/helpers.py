"""Shared test helpers for retracesoftware integration tests."""
import os
import sys
import subprocess

PYTHON = sys.executable
TIMEOUT = 30


def run_record(script_path, recording, extra_args=None, env=None):
    """Run a script under retrace recording.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
        "--format", "unframed_binary",
        "--stacktraces",
        "--", str(script_path),
    ]
    if extra_args:
        cmd.extend(extra_args)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=run_env,
    )


def run_replay(recording, extra_args=None, env=None):
    """Replay from a trace file.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
    ]
    if extra_args:
        cmd.extend(extra_args)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=run_env,
    )
