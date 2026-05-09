"""Regression for replaying externally delivered SIGINT handlers."""

from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import sys
import textwrap

import pytest


def test_external_sigint_handler_side_effect_replays(tmp_path: Path):
    script = tmp_path / "external_sigint_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import signal
            import time

            should_exit = False


            def handler(sig, frame):
                global should_exit
                print("handled", sig, flush=True)
                should_exit = True


            signal.signal(signal.SIGINT, handler)
            print("ready", flush=True)

            while True:
                time.sleep(0.05)
                time.monotonic()
                if should_exit:
                    break

            print("done", flush=True)
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    record = subprocess.Popen(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert record.stdout is not None
        ready_line = record.stdout.readline()
        assert ready_line == "ready\n"

        record.send_signal(signal.SIGINT)
        record_stdout_tail, record_stderr = record.communicate(timeout=10)
    finally:
        if record.poll() is None:
            record.kill()
            record.wait(timeout=5)

    record_stdout = ready_line + record_stdout_tail
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record_stdout}\n"
        f"stderr:\n{record_stderr}"
    )
    assert "handled 2" in record_stdout
    assert "done" in record_stdout

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    try:
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
            timeout=5,
            env=replay_env,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "replay timed out before the recorded SIGINT handler side effect "
            f"was delivered\nstdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
        )

    assert replay.returncode == 0, (
        f"replay failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "handled 2" in replay.stdout
    assert "done" in replay.stdout
