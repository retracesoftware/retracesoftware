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
from helpers import run_record, run_replay

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
         "--format", "unframed_binary",
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


@pytest.mark.xfail(
    reason="FastAPI TestClient replay still diverges on internal thread/time bookkeeping",
    strict=False,
)
def test_record_then_replay_fastapi_testclient_request(tmpdir):
    pytest.importorskip("fastapi")

    script_file = Path(tmpdir) / "fastapi_testclient.py"
    script_file.write_text(
        """\
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/")
def read_root():
    print("Root endpoint was called", flush=True)
    return {"message": "Hello, FastAPI"}


if __name__ == "__main__":
    print("=== fastapi_test ===", flush=True)
    client = TestClient(app)
    print("Testing root endpoint...", flush=True)
    response = client.get("/")
    print(f"Response status: {response.status_code}", flush=True)
    print(f"Response body: {response.json()}", flush=True)
""",
        encoding="utf-8",
    )

    modules_dir = Path(tmpdir) / "modules"
    modules_dir.mkdir()
    (modules_dir / "mmap.toml").write_text(
        'proxy = ["mmap"]\nimmutable = ["error"]\n',
        encoding="utf-8",
    )

    trace_file = str(Path(tmpdir) / "fastapi_test.retrace")
    env = {
        "RETRACE_MODULES_PATH": str(modules_dir),
    }

    record = run_record(script_file, trace_file, env=env)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file, env=env)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
