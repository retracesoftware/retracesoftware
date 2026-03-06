"""Fork safety tests for the writer.

Verifies that os.fork() is handled correctly at the writer level:
- Parent and child both write PID-framed data to the same file
- list_pids can extract all PIDs from the framed file

Reading PID-framed data back requires the Go demux pipeline to
split per-PID streams before the reader sees them.
"""
import os
import sys
import time
import pytest

pytest.importorskip("retracesoftware.stream")
stream = pytest.importorskip("retracesoftware.stream")

if sys.platform == "win32":
    pytest.skip("fork not available on Windows", allow_module_level=True)


def _thread_id() -> str:
    return "main-thread"


def test_list_pids(tmp_path):
    """list_pids returns all unique PIDs present in a PID-framed trace."""
    path = tmp_path / "trace.bin"

    with stream.writer(path, thread=_thread_id, flush_interval=0.01) as w:
        w("from_parent")
        w.flush()

        pid = os.fork()
        if pid == 0:
            w("from_child")
            w.flush()
            time.sleep(0.05)
            os._exit(0)

        os.waitpid(pid, 0)
        w.flush()

    pids = stream.list_pids(path)
    assert len(pids) >= 2
    assert os.getpid() in pids
    assert pid in pids
