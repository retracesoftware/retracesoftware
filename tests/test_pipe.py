"""Tests for named-pipe (FIFO) based record/replay infrastructure.

Verifies that retrace record can write its trace stream through a
named pipe, which is the foundation for concurrent record+replay.
"""
import os
import sys
import stat
import tempfile
import shutil
import threading
import subprocess

import pytest

PYTHON = sys.executable
TIMEOUT = 30


@pytest.fixture
def pipedir():
    """A temp directory with a named pipe at trace.bin."""
    d = tempfile.mkdtemp(prefix="retrace_pipe_test_")
    pipe_path = os.path.join(d, "trace.bin")
    os.mkfifo(pipe_path)
    yield d
    shutil.rmtree(d, ignore_errors=True)


SIMPLE_SCRIPT = """\
import time
print(time.time())
"""


def drain_pipe(pipe_path, result):
    """Read all bytes from a FIFO until EOF, storing them in result['data']."""
    with open(pipe_path, "rb") as f:
        result["data"] = f.read()


def test_fifo_exists(pipedir):
    """Sanity check: the fixture creates a valid FIFO."""
    pipe_path = os.path.join(pipedir, "trace.bin")
    assert os.path.exists(pipe_path)
    assert stat.S_ISFIFO(os.stat(pipe_path).st_mode)


def test_record_writes_to_pipe(pipedir):
    """Record via 'python -m retracesoftware', draining the FIFO from a thread.

    Asserts that:
    - The record process exits successfully (exit code 0).
    - Nonzero bytes were written through the pipe.
    """
    pipe_path = os.path.join(pipedir, "trace.bin")

    drain_result = {}
    reader_thread = threading.Thread(
        target=drain_pipe, args=(pipe_path, drain_result)
    )
    reader_thread.start()

    script_file = os.path.join(pipedir, "target.py")
    with open(script_file, "w") as f:
        f.write(SIMPLE_SCRIPT)

    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", pipedir,
        "--create_tracedir_cmd", "true",
        "--", script_file,
    ]
    rec = subprocess.run(
        cmd, capture_output=True, text=True, timeout=TIMEOUT,
    )

    reader_thread.join(timeout=TIMEOUT)

    print("record stdout:", rec.stdout)
    print("record stderr:", rec.stderr)

    assert rec.returncode == 0, f"Record failed (exit {rec.returncode}):\n{rec.stderr}"
    assert not reader_thread.is_alive(), "Reader thread did not finish"
    assert len(drain_result.get("data", b"")) > 0, "No bytes read from pipe"
