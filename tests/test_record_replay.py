"""Basic end-to-end record/replay tests.

Records a trivial script to disk, then replays from disk.
Verifies exit codes match and stdout is identical.
"""
import os
import sys
import tempfile
import shutil

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
