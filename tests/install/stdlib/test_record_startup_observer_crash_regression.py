"""Regression: quit-on-error startup crashes when stacktrace callback is None.

Observed from Flask debug scenario (and any debug record path):
- `--quit_on_error` enables wrapping writer call-sites with `utils.observer`
- `install.stream_writer(...).stacktrace` is `None` when `--stacktraces` is off
- `bind_write_error(None)` passes `function=None` into native observer init
- native init dereferences it and process exits with a fatal signal

This test isolates the root component path to retrace startup itself by using
the smallest possible target script.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="regression reproduced on Python 3.12 startup path",
)
def test_record_startup_does_not_crash_utils_observer_init(tmp_path: Path):
    script = tmp_path / "hello.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--quit_on_error",
            "--raw",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        f"record startup crashed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )

    # Control check: with stacktrace callback enabled the same path is healthy.
    control = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(tmp_path / "trace_stack.retrace"),
            "--quit_on_error",
            "--stacktraces",
            "--raw",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert control.returncode == 0, (
        f"control run with --stacktraces failed (exit {control.returncode})\n"
        f"stdout:\n{control.stdout}\n"
        f"stderr:\n{control.stderr}"
    )
