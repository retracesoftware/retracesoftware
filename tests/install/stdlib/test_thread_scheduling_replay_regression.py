"""Regression coverage for deterministic replay thread scheduling."""

from __future__ import annotations

import os
from pathlib import Path
import threading

from tests.helpers import run_record, run_replay
from tests.runner import Runner


def _assert_semaphore_branch_replays_thread_birth(**lane):
    sem = threading.Semaphore(1)
    started = []

    def worker():
        started.append(threading.current_thread().name)

    def work():
        before = len(started)
        if sem.acquire(timeout=0):
            thread = threading.Thread(target=worker, name=f"worker-{len(started)}")
            thread.start()
            thread.join(timeout=5)
        return len(started) - before

    runner = Runner(matrix=({"name": "lane", **lane},))
    assert runner.run(work) == 1


def test_semaphore_scheduling_branch_replays_thread_birth():
    """A recorded semaphore branch must start the same logical child thread."""

    _assert_semaphore_branch_replays_thread_birth(
        debug=False,
        stacktraces=False,
    )


def test_semaphore_scheduling_branch_replays_thread_birth_with_debug():
    """Debug checkpoints must not change deterministic thread birth."""

    _assert_semaphore_branch_replays_thread_birth(
        debug=True,
        stacktraces=False,
    )


def test_semaphore_scheduling_branch_replays_thread_birth_with_stacktraces():
    """Stacktrace mode must keep the same synchronization branch behavior."""

    _assert_semaphore_branch_replays_thread_birth(
        debug=False,
        stacktraces=True,
    )


def test_cli_stacktrace_semaphore_checkpoint_is_stable(tmp_path: Path):
    """Binary stacktrace checkpoints should not serialize live Semaphores."""

    script = tmp_path / "semaphore_checkpoint.py"
    script.write_text(
        (
            "import threading\n"
            "\n"
            "sem = threading.Semaphore(0)\n"
            "print('first', sem.acquire(timeout=0), flush=True)\n"
            "sem.release()\n"
            "print('second', sem.acquire(timeout=0), flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = run_record(str(script), str(recording), env=env, stacktraces=True)
    assert record.returncode == 0, (
        "record failed for semaphore checkpoint reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for semaphore checkpoint reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
