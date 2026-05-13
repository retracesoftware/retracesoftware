"""Regression: record hangs at shutdown with live ThreadPoolExecutor workers.

User-facing symptom:
- `dockertests/tests/asgiref_test/test.py` prints success under debug record
- process then hangs until manually interrupted (Ctrl-C)

Root component path:
- `retracesoftware.run.run_with_retrace` currently calls
  `wait_for_non_daemon_threads()` *before* running Python atexit hooks.
- For thread-pool workers (used by `asgiref.sync_to_async`), the cleanup that
  stops non-daemon threads is registered in atexit.
- Waiting first causes a shutdown deadlock/hang.

This test reproduces the same lifecycle issue with only stdlib components.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from tests.helpers import PYTHON


def test_record_does_not_hang_when_threadpool_cleanup_is_atexit(tmp_path: Path):
    script = tmp_path / "threadpool_hang_repro.py"
    script.write_text(
        (
            "import asyncio\n"
            "from concurrent.futures import ThreadPoolExecutor\n"
            "executor = ThreadPoolExecutor(max_workers=1)\n"
            "async def main():\n"
            "    loop = asyncio.get_running_loop()\n"
            "    await loop.run_in_executor(executor, lambda: 1)\n"
            "    print('ok', flush=True)\n"
            "asyncio.run(main())\n"
        ),
        encoding="utf-8",
    )

    # Control: plain Python exits normally.
    plain = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert plain.returncode == 0, (
        f"plain run failed (exit {plain.returncode})\n"
        f"stdout:\n{plain.stdout}\n"
        f"stderr:\n{plain.stderr}"
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["RETRACE_CONFIG"] = "debug"
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    try:
        proc = subprocess.run(
            [
                PYTHON,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
                "--format",
                "unframed_binary",
                "--",
                str(script),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "record run hung (timeout) after script completed; "
            "this matches the asgiref record hang shutdown bug"
        ) from exc

    assert proc.returncode == 0, (
        f"record run failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_install_retrace_does_not_patch_native_thread_start():
    """Native thread startup is owned by retrace-python, not retracesoftware wrappers."""

    import _thread
    import threading

    from retracesoftware.install import install_retrace
    from retracesoftware.proxy.io import recorder

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    original_start_new_thread = _thread.start_new_thread
    original_threading_start_new_thread = threading._start_new_thread
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        assert _thread.start_new_thread is original_start_new_thread
        assert threading._start_new_thread is original_threading_start_new_thread
    finally:
        uninstall()


def test_new_thread_defaults_to_internal_gate():
    """Application threads start retrace-enabled without start interception."""

    import threading

    from retracesoftware.install import install_retrace
    from retracesoftware.proxy.io import recorder

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        enabled_states = []

        def target():
            enabled_states.append(system.enabled())

        thread = threading.Thread(target=target)
        tape.clear()
        system.run(thread.start)
        with system.disable():
            thread.join(timeout=5)

        assert enabled_states == [True]
        assert "ON_START" in tape
    finally:
        uninstall()


def test_disable_context_clears_gate_inside_thread_body():
    """Control-plane bodies can explicitly opt out of the default internal gate."""

    import threading

    from retracesoftware.install import install_retrace
    from retracesoftware.proxy.io import recorder

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        enabled_states = []

        def target():
            enabled_states.append(system.enabled())
            with system.disable():
                enabled_states.append(system.enabled())
            enabled_states.append(system.enabled())

        thread = threading.Thread(target=target)
        tape.clear()
        system.run(thread.start)
        with system.disable():
            thread.join(timeout=5)

        assert enabled_states == [True, False, True]
    finally:
        uninstall()


def test_direct_start_new_thread_defaults_to_internal_gate():
    """The lower-level _thread API also relies on default gate state."""

    import _thread
    import time

    from retracesoftware.install import install_retrace
    from retracesoftware.proxy.io import recorder

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        enabled_states = []

        def target():
            enabled_states.append(system.enabled())

        tape.clear()
        system.run(_thread.start_new_thread, target, ())
        deadline = time.time() + 5
        with system.disable():
            while not enabled_states and time.time() < deadline:
                time.sleep(0.01)

        assert enabled_states == [True]
        assert "ON_START" in tape
    finally:
        uninstall()


def test_stacktrace_replay_normalizes_exception_exit_checkpoint(tmp_path: Path):
    """Debug checkpoints do not serialize live exception tracebacks."""

    script = tmp_path / "rlock_exception_exit.py"
    script.write_text(
        (
            "import threading\n"
            "\n"
            "lock = threading.RLock()\n"
            "try:\n"
            "    with lock:\n"
            "        raise AttributeError('boom')\n"
            "except AttributeError:\n"
            "    pass\n"
            "print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--format",
            "unframed_binary",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert record.returncode == 0, (
        f"record run failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay run failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == "ok\n"


def test_stacktrace_replay_normalizes_ssl_descriptor_checkpoint(tmp_path: Path):
    """SSL descriptor and enum checkpoints compare by stable semantics."""

    script = tmp_path / "ssl_minimum_version.py"
    script.write_text(
        (
            "import ssl\n"
            "\n"
            "ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)\n"
            "ctx.minimum_version = ssl.TLSVersion.TLSv1_2\n"
            "print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--format",
            "unframed_binary",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert record.returncode == 0, (
        f"record run failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay run failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == "ok\n"


def test_asyncio_default_executor_replay_wakeup_progresses(tmp_path: Path):
    """Executor completion schedules the loop through the live wakeup path."""

    script = tmp_path / "asyncio_executor_replay.py"
    script.write_text(
        (
            "import asyncio\n"
            "\n"
            "async def one(value):\n"
            "    loop = asyncio.get_running_loop()\n"
            "    return await loop.run_in_executor(None, lambda: value * 2)\n"
            "\n"
            "async def main():\n"
            "    for value in range(5):\n"
            "        print('value', await one(value), flush=True)\n"
            "\n"
            "asyncio.run(main())\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["RETRACE_CONFIG"] = "debug"
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert record.returncode == 0, (
        f"record run failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [PYTHON, "-m", "retracesoftware", "--recording", str(recording)],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay run failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def test_replay_post_run_daemon_thread_cleanup_does_not_consume_trace(
    tmp_path: Path,
):
    """Daemon cleanup after the trace window should not keep replay gateways live."""

    script = tmp_path / "daemon_atexit_cleanup.py"
    script.write_text(
        (
            "import atexit\n"
            "import threading\n"
            "\n"
            "ready = threading.Event()\n"
            "\n"
            "def worker():\n"
            "    ready.wait()\n"
            "    print('worker flushed', flush=True)\n"
            "\n"
            "thread = threading.Thread(target=worker, daemon=True, name='flush-worker')\n"
            "thread.start()\n"
            "\n"
            "def shutdown():\n"
            "    ready.set()\n"
            "    thread.join(timeout=5)\n"
            "    print('shutdown done', flush=True)\n"
            "\n"
            "atexit.register(shutdown)\n"
            "print('main done', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert record.returncode == 0, (
        f"record run failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--read_timeout",
            "1000",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay run failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stderr == ""
    assert replay.stdout == record.stdout
