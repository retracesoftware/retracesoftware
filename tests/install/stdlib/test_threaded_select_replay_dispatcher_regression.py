"""Regression: replay thread dispatch diverges on child-thread select().

Root component focus:
- replay-side per-thread routing in `stream.reader.DemuxReader`
- native `utils.Dispatcher` that hands recorded events to replay threads

Ownership signal:
- the FastAPI TestClient replay failure reduces to a stdlib child thread that
  performs `select.select()`
- keeping only `select.select` proxied is still sufficient to reproduce the
  same `Dispatcher: too many threads waiting for item` replay failure
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from tests.helpers import PYTHON, retrace_env, run_record, run_replay


@pytest.mark.skipif(
    not hasattr(socket, "socketpair"),
    reason="socketpair is required for this regression test",
)
def test_replay_child_thread_select_does_not_diverge(tmp_path: Path):
    script = tmp_path / "thread_select_repro.py"
    script.write_text(
        (
            "import select\n"
            "import socket\n"
            "import threading\n"
            "\n"
            "box = {}\n"
            "\n"
            "def worker():\n"
            "    try:\n"
            "        left, right = socket.socketpair()\n"
            "        right.send(b'x')\n"
            "        readable, _, _ = select.select([left], [], [], 0.1)\n"
            "        box['count'] = len(readable)\n"
            "        left.close()\n"
            "        right.close()\n"
            "    except BaseException as exc:\n"
            "        box['error'] = f'{type(exc).__name__}: {exc}'\n"
            "\n"
            "def main():\n"
            "    thread = threading.Thread(target=worker)\n"
            "    thread.start()\n"
            "    thread.join()\n"
            "    assert box.get('error') is None, box['error']\n"
            "    assert box.get('count') == 1, box\n"
            "    print('ok', flush=True)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        encoding="utf-8",
    )

    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "select.toml").write_text(
        'proxy = ["select"]\n',
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)
    env["RETRACE_MODULES_PATH"] = str(modules_dir)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for child-thread select reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for child-thread select reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "pipe"),
    reason="selectable pipe file descriptors are required for this regression test",
)
def test_replay_selector_pipe_woken_by_exiting_child_does_not_diverge(
    tmp_path: Path,
):
    script = tmp_path / "thread_selector_pipe_wakeup_exit_repro.py"
    script.write_text(
        (
            "import os\n"
            "import selectors\n"
            "import threading\n"
            "\n"
            "for value in range(20):\n"
            "    reader, writer = os.pipe()\n"
            "    selector = selectors.DefaultSelector()\n"
            "    selector.register(reader, selectors.EVENT_READ)\n"
            "\n"
            "    def worker(fd=writer):\n"
            "        os.write(fd, b'x')\n"
            "        os.close(fd)\n"
            "\n"
            "    thread = threading.Thread(target=worker)\n"
            "    thread.start()\n"
            "    events = selector.select(timeout=5.0)\n"
            "    print('ready', value, len(events), flush=True)\n"
            "    print('data', value, os.read(reader, 1).decode(), flush=True)\n"
            "    thread.join(timeout=2.0)\n"
            "    print('joined', value, not thread.is_alive(), flush=True)\n"
            "    selector.unregister(reader)\n"
            "    selector.close()\n"
            "    os.close(reader)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for selector pipe wakeup/thread-exit reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    expected_stdout = "".join(
        f"ready {value} 1\ndata {value} x\njoined {value} True\n"
        for value in range(20)
    )
    assert record.stdout == expected_stdout

    try:
        replay = subprocess.run(
            [PYTHON, "-m", "retracesoftware", "--recording", str(recording)],
            capture_output=True,
            text=True,
            timeout=12,
            env=retrace_env(env, PYTHON),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise AssertionError(
            "replay timed out after repeated child-thread pipe wakeups\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc

    assert replay.returncode == 0, (
        "replay diverged for selector pipe wakeup/thread-exit reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.skipif(
    not hasattr(socket, "socketpair"),
    reason="socketpair is required for this regression test",
)
def test_replay_main_thread_select_woken_by_exiting_child_does_not_hang(tmp_path: Path):
    script = tmp_path / "thread_select_wakeup_exit_repro.py"
    script.write_text(
        (
            "import select\n"
            "import socket\n"
            "import threading\n"
            "\n"
            "reader, writer = socket.socketpair()\n"
            "\n"
            "def worker():\n"
            "    writer.send(b'x')\n"
            "    writer.close()\n"
            "\n"
            "thread = threading.Thread(target=worker)\n"
            "thread.start()\n"
            "ready, _, _ = select.select([reader], [], [], 5.0)\n"
            "print('ready', len(ready), flush=True)\n"
            "print('data', reader.recv(1).decode(), flush=True)\n"
            "thread.join(timeout=2.0)\n"
            "print('joined', not thread.is_alive(), flush=True)\n"
            "reader.close()\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for main-thread select wakeup/thread-exit reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert record.stdout == "ready 1\ndata x\njoined True\n"

    try:
        replay = subprocess.run(
            [PYTHON, "-m", "retracesoftware", "--recording", str(recording)],
            capture_output=True,
            text=True,
            timeout=8,
            env=retrace_env(env, PYTHON),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise AssertionError(
            "replay timed out after a child thread woke select() and exited\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc

    assert replay.returncode == 0, (
        "replay diverged for main-thread select wakeup/thread-exit reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
