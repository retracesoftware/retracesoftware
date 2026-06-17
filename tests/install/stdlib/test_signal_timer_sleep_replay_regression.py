"""Regression for SIGALRM interrupting a recorded sleep boundary."""

from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import sys
import textwrap

import pytest


def test_setitimer_handler_interrupting_sleep_replays(tmp_path: Path) -> None:
    if not hasattr(signal, "setitimer"):
        pytest.skip("setitimer is not available on this platform")

    script = tmp_path / "signal_timer_sleep_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import signal
            import time


            class AlarmRaised(Exception):
                pass


            def handler(signum, frame):
                print(f"handler {signum}", flush=True)
                raise AlarmRaised("timer fired")


            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, 0.05)

            try:
                time.sleep(1.0)
            except AlarmRaised as exc:
                print(f"caught {exc}", flush=True)
            else:
                raise SystemExit("timer did not interrupt sleep")
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)

            print("done", flush=True)
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    recording = tmp_path / "signal-timer-sleep.retrace"

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
    assert "handler" in record.stdout
    assert "caught timer fired" in record.stdout
    assert "done" in record.stdout

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
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
