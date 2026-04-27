"""Regression: AnyIO task groups with module-level random delays replay cleanly.

This mirrors the old ``dockertests/anyio_test`` shape: AnyIO schedules a few
tasks with delays generated through ``random.uniform``. The important proxy
surface is the pre-existing ``random._inst`` singleton, whose methods are used
by module-level random helpers after ``_random.Random`` has been patched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


def test_replay_anyio_task_group_with_random_delays_does_not_diverge(tmp_path: Path):
    pytest.importorskip("anyio")

    script = tmp_path / "anyio_random_task_group_repro.py"
    script.write_text(
        (
            "import random\n"
            "import anyio\n"
            "\n"
            "\n"
            "async def task(name, delay):\n"
            "    print(f'task {name} start {delay:.5f}', flush=True)\n"
            "    await anyio.sleep(delay)\n"
            "    print(f'task {name} done', flush=True)\n"
            "\n"
            "\n"
            "async def main():\n"
            "    print('=== anyio_random_task_group ===', flush=True)\n"
            "    async with anyio.create_task_group() as task_group:\n"
            "        for name in ('A', 'B', 'C'):\n"
            "            delay = random.uniform(0.001, 0.003)\n"
            "            print(f'delay {name} {delay:.5f}', flush=True)\n"
            "            task_group.start_soon(task, name, delay)\n"
            "    print('all done', flush=True)\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    anyio.run(main)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for AnyIO random task-group reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for AnyIO random task-group reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
