"""Basic end-to-end record/replay tests.

Records a trivial script to disk, then replays from disk.
Verifies exit codes match and stdout is identical.
"""
import os
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

import pytest

from run_record_replay import record_then_replay

PYTHON = sys.executable
TIMEOUT = 30

HELLO_SCRIPT = """\
import time
print(time.time())
"""


@pytest.fixture
def tmpdir():
    d = tempfile.mkdtemp(prefix="retrace_e2e_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_record_then_replay(tmpdir):
    """Record a trivial script, then replay it.

    Checks:
    - record exits 0 and produces a trace.bin
    - replay exits 0
    - stdout matches between record and replay
    """
    script_file = os.path.join(tmpdir, "hello.py")
    with open(script_file, "w") as f:
        f.write(HELLO_SCRIPT)

    record_then_replay(tmpdir, script_file)


def test_record_then_replay_threading_single(tmpdir):
    trace_file = os.path.join(tmpdir, "threading.retrace")
    script_file = Path(__file__).parent / "scripts" / "threading_single.py"

    record = subprocess.run(
        [PYTHON, "-m", "retracesoftware",
         "--recording", trace_file,
         "--raw",
         "--", str(script_file)],
        capture_output=True, text=True, timeout=TIMEOUT,
    )

    assert record.returncode == 0, f"Record failed (exit {record.returncode}):\n{record.stderr}"

    replay = subprocess.run(
        [PYTHON, "-m", "retracesoftware",
         "--recording", trace_file],
        capture_output=True, text=True, timeout=TIMEOUT,
    )

    assert replay.returncode == 0, f"Replay failed (exit {replay.returncode}):\n{replay.stderr}"
    assert replay.stdout == record.stdout == "worker ran\n"
