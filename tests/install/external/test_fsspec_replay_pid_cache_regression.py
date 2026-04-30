"""Regression coverage for fsspec filesystem cache PID replay."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


def test_replay_fsspec_memory_filesystem_cache_pid_does_not_diverge(tmp_path: Path):
    pytest.importorskip("fsspec")

    script = tmp_path / "fsspec_memory_repro.py"
    script.write_text(
        (
            "import fsspec\n"
            "\n"
            "fs = fsspec.filesystem('memory')\n"
            "print('fs-made', type(fs).__name__, flush=True)\n"
            "fs.pipe('/retrace/test.txt', b'hello')\n"
            "fs.copy('/retrace/test.txt', '/retrace/test_copy.txt')\n"
            "fs.move('/retrace/test_copy.txt', '/retrace/test_moved.txt')\n"
            "print(fs.cat('/retrace/test_moved.txt').decode(), flush=True)\n"
            "print(fs.exists('/retrace/test_copy.txt'), flush=True)\n"
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
        "record failed for fsspec memory filesystem reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged in fsspec filesystem cache PID handling\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
