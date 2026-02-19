"""Shared fixtures for retracesoftware whole-process tests.

These tests invoke `python -m retracesoftware` as a subprocess for
record and replay, verifying end-to-end behaviour including fork/exec
scenarios.
"""
import os
os.environ["RETRACE_DEBUG"] = "1"

import sys
import shutil
import tempfile
import subprocess

import pytest


PYTHON = sys.executable
TIMEOUT = 30  # seconds


@pytest.fixture
def tmpdir():
    """A fresh temporary directory, cleaned up after the test."""
    d = tempfile.mkdtemp(prefix="retrace_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def run_record(script_path, recording, extra_args=None, env=None):
    """Run a script under retrace recording.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.bin``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
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

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.bin``).
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
