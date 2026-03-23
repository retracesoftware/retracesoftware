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
    reason="Replay can reorder plain cross-thread Future handoff",
    strict=False,
)
def test_record_then_replay_concurrent_future_handoff(tmpdir):
    trace_file = os.path.join(tmpdir, "future_handoff.retrace")
    script_file = Path(tmpdir) / "future_handoff.py"
    script_file.write_text(
        """\
import threading
from concurrent.futures import Future

for i in range(5):
    future = Future()

    def worker():
        print(f"worker_before {i}", flush=True)
        future.set_result(123)
        print(f"worker_after {i}", flush=True)

    thread = threading.Thread(target=worker)
    thread.start()
    print(f"main_result {i} {future.result()}", flush=True)
    thread.join()
""",
        encoding="utf-8",
    )

    record = run_record(script_file, trace_file)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.xfail(
    reason="Replay can report lock.acquire() succeeded without locking after thread start",
    strict=False,
)
def test_record_then_replay_plain_lock_acquire_state_after_thread_start(tmpdir):
    trace_file = os.path.join(tmpdir, "plain_lock_acquire_state.retrace")
    script_file = Path(tmpdir) / "plain_lock_acquire_state.py"
    script_file.write_text(
        """\
import _thread
import threading

for _ in range(5):
    lock = _thread.allocate_lock()

    def worker():
        pass


    def helper():
        acquired = lock.acquire()
        try:
            return acquired, lock.locked()
        finally:
            if acquired:
                lock.release()


    thread = threading.Thread(target=worker)
    thread.start()
    result = helper()
    thread.join()
    print(result, flush=True)
""",
        encoding="utf-8",
    )

    record = run_record(script_file, trace_file)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.xfail(
    reason="Replay can lose RLock ownership across a Python __enter__ wrapper",
    strict=False,
)
def test_record_then_replay_rlock_enter_wrapper_after_thread_stdout(tmpdir):
    trace_file = os.path.join(tmpdir, "rlock_enter_wrapper.retrace")
    script_file = Path(tmpdir) / "rlock_enter_wrapper.py"
    script_file.write_text(
        """\
import sys
import threading
import time

lock = threading.RLock()


def worker():
    sys.stdout.write("worker\\n")
    sys.stdout.flush()
    time.sleep(0.01)


def enter_wrapper():
    return lock.__enter__()


def helper():
    enter_wrapper()
    try:
        return lock._is_owned()
    finally:
        lock.__exit__(None, None, None)


thread = threading.Thread(target=worker)
thread.start()
result = helper()
thread.join()
print(result, flush=True)
""",
        encoding="utf-8",
    )

    record = run_record(script_file, trace_file)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.parametrize(
    ("factory_expr", "name"),
    [
        ("_thread.allocate_lock()", "lock"),
        ("_thread.RLock()", "rlock"),
    ],
)
def test_record_then_replay_thread_lock_operations(tmpdir, factory_expr, name):
    trace_file = os.path.join(tmpdir, f"{name}.retrace")
    script_file = Path(tmpdir) / f"{name}.py"
    script_file.write_text(
        f"""\
import _thread

lock = {factory_expr}
print(type(lock).__module__, type(lock).__name__, flush=True)
print(lock.acquire(), flush=True)
lock.release()
print(lock.acquire(False), flush=True)
lock.release()
print("done", flush=True)
""",
        encoding="utf-8",
    )

    record = run_record(script_file, trace_file)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.xfail(
    reason="Replay diverges on stdlib cross-thread asyncio/future coordination",
    strict=False,
)
def test_record_then_replay_asyncio_run_coroutine_threadsafe(tmpdir):
    trace_file = os.path.join(tmpdir, "asyncio_threadsafe.retrace")
    script_file = Path(tmpdir) / "asyncio_threadsafe.py"
    script_file.write_text(
        """\
import asyncio
import threading
from concurrent.futures import Future

loop_ready = Future()


def runner():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop_ready.set_result(loop)
    try:
        loop.run_forever()
    finally:
        loop.close()


thread = threading.Thread(target=runner, daemon=True)
thread.start()
loop = loop_ready.result()
future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result=123), loop)
print(future.result(), flush=True)
loop.call_soon_threadsafe(loop.stop)
thread.join()
""",
        encoding="utf-8",
    )

    record = run_record(script_file, trace_file)
    assert record.returncode == 0, (
        f"Record failed (exit {record.returncode}):\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file)
    assert replay.returncode == 0, (
        f"Replay failed (exit {replay.returncode}):\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


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
