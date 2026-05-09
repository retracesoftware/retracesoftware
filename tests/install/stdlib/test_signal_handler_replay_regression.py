"""Regression for replaying Python signal handlers as callbacks."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def test_replay_runs_handler_registered_with_signal_signal(tmp_path: Path):
    if not hasattr(signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 is not available on this platform")

    script = tmp_path / "signal_handler_repro.py"
    script.write_text(
        (
            "import signal\n"
            "\n"
            "handled = []\n"
            "\n"
            "def handler(sig, frame):\n"
            "    handled.append(sig)\n"
            "    print('handled', sig, flush=True)\n"
            "\n"
            "signal.signal(signal.SIGUSR1, handler)\n"
            "signal.raise_signal(signal.SIGUSR1)\n"
            "print('count', len(handled), flush=True)\n"
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    recording = tmp_path / "signal.retrace"

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
        timeout=30,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\nstderr:\n{record.stderr}"
    )
    assert "count 1" in record.stdout

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=replay_env,
    )
    assert replay.returncode == 0, (
        f"replay failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\nstderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
