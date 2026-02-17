"""Shared fixtures for retracesoftware whole-process tests.

These tests invoke `python -m retracesoftware` as a subprocess for
record and replay, verifying end-to-end behaviour including fork/exec
scenarios.
"""
import os
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


def run_record(script_path, recording_dir, extra_args=None, env=None):
    """Run a script under retrace recording.

    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording_dir,
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


def run_replay(recording_dir, extra_args=None, env=None):
    """Replay from a recording directory.

    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording_dir,
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
