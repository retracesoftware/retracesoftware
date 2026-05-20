"""Regression coverage for deterministic replay thread scheduling."""

from __future__ import annotations

import os
from pathlib import Path
import threading

from tests.helpers import run_record, run_replay
from tests.runner import Runner


def _assert_semaphore_branch_replays_thread_birth(**lane):
    def work():
        sem = threading.Semaphore(1)
        started = []

        def worker():
            started.append(threading.current_thread().name)

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


def test_cli_thread_switch_smoke_replays_order_dependent_digest(tmp_path: Path):
    """Replay must preserve a recorded schedule-derived thread digest."""

    script = tmp_path / "thread_switch_digest_stress.py"
    script.write_text(
        (
            "import sys\n"
            "import threading\n"
            "\n"
            "sys.setswitchinterval(1e-6)\n"
            "\n"
            "THREADS = 2\n"
            "ITERATIONS = 16\n"
            "MASK = (1 << 64) - 1\n"
            "GOLDEN = 0x9E3779B97F4A7C15\n"
            "\n"
            "events = []\n"
            "\n"
            "\n"
            "def worker(tid):\n"
            "    for _ in range(ITERATIONS):\n"
            "        events.append(tid)\n"
            "\n"
            "\n"
            "threads = [\n"
            "    threading.Thread(target=worker, args=(tid,), name=f'stress-{tid}')\n"
            "    for tid in range(THREADS)\n"
            "]\n"
            "for thread in threads:\n"
            "    thread.start()\n"
            "for thread in threads:\n"
            "    thread.join()\n"
            "\n"
            "seen = [0] * THREADS\n"
            "acc = 0x123456789ABCDEF0\n"
            "for index, tid in enumerate(events):\n"
            "    seen[tid] += 1\n"
            "    word = (\n"
            "        ((tid + 1) * GOLDEN)\n"
            "        ^ (index * 0xBF58476D1CE4E5B9)\n"
            "    ) & MASK\n"
            "    acc ^= word\n"
            "    acc = ((acc << 11) | (acc >> 53)) & MASK\n"
            "    acc = (acc * 0xD6E8FEB86659FD93 + 0xA5A5A5A5A5A5A5A5) & MASK\n"
            "\n"
            "assert len(events) == THREADS * ITERATIONS, len(events)\n"
            "assert seen == [ITERATIONS] * THREADS, seen\n"
            "queue = ','.join(str(tid) for tid in events)\n"
            "print(f'events={len(events)} digest={acc:016x}', flush=True)\n"
            "print(f'queue={queue}', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_SKIP_CHECKSUMS"] = "1"

    record = run_record(str(script), str(recording), env=env, stacktraces=False)
    assert record.returncode == 0, (
        "record failed for thread switch digest stress test\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert record.stdout.startswith("events=32 digest=")

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for thread switch digest stress test\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout, (
        "replay produced a different schedule-derived digest\n"
        f"record stdout:\n{record.stdout}\n"
        f"replay stdout:\n{replay.stdout}\n"
    )
