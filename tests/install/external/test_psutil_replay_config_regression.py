"""Regression coverage for psutil process introspection replay."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


pytest.importorskip("psutil")


def test_replay_psutil_process_uses_recorded_process_state(tmp_path: Path):
    script = tmp_path / "psutil_process_repro.py"
    script.write_text(
        (
            "import os\n"
            "import psutil\n"
            "\n"
            "print(f'pid={os.getpid()}', flush=True)\n"
            "process = psutil.Process()\n"
            "memory_info = process.memory_info()\n"
            "print(f'rss-positive={memory_info.rss > 0}', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env, stacktraces=False)
    assert record.returncode == 0, (
        "record failed for psutil Process reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay used live psutil process state instead of the recording\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
