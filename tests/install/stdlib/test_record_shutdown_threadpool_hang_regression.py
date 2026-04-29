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

    try:
        proc = subprocess.run(
            [
                sys.executable,
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


def test_asyncio_shutdown_thread_stays_retraced_and_threadsafe_schedule_emits_sync():
    """The shutdown thread stays retraced; cross-thread scheduling emits SYNC."""

    import asyncio
    import threading

    from retracesoftware.install import install_retrace
    from retracesoftware.proxy.io import recorder
    from retracesoftware.proxy.system import _is_disabled_thread_target

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        loop = asyncio.new_event_loop()
        try:
            target = loop._do_shutdown
            underlying = getattr(target, "__func__", target)
            assert not getattr(underlying, "__retrace_disabled_thread_target__", False)

            thread = threading.Thread(target=target, args=(loop.create_future(),))
            assert not _is_disabled_thread_target(thread._bootstrap)

            system.run(loop.call_soon_threadsafe, lambda: None)
            assert "SYNC" in tape
        finally:
            loop.close()
    finally:
        uninstall()


def test_asyncio_default_executor_replay_wakeup_progresses(tmp_path: Path):
    """Executor completion schedules the loop with a live wakeup plus SYNC."""

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

    record = subprocess.run(
        [
            sys.executable,
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
        [sys.executable, "-m", "retracesoftware", "--recording", str(recording)],
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
