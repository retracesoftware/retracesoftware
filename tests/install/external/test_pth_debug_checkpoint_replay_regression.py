"""Regression: pth+debug recording can replay ResultMessage as Checkpoint.

The public pth recording hook works for this small psutil program without
``RETRACE_CONFIG=debug``.  The direct wrapper flow also works with debug:

    RETRACE_CONFIG=debug python -m retracesoftware --recording trace.retrace -- app.py

The failing shape is the local dockertest/manual pth flow:

    RETRACE_RECORDING=trace.retrace RETRACE_CONFIG=debug python app.py

For psutil/memray and some Flask server recordings, PidFile replay starts
normally and then sees a protocol ``ResultMessage`` where it expects the next
``CheckpointMessage``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "pth auto-enable plus RETRACE_CONFIG=debug can desync replay protocol "
        "messages: ResultMessage is delivered where CheckpointMessage is expected"
    ),
)
def test_pth_debug_psutil_pidfile_replay_keeps_checkpoint_alignment(
    tmp_path: Path,
):
    pytest.importorskip("psutil")

    script = tmp_path / "psutil_pth_debug_checkpoint_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import psutil


            if __name__ == "__main__":
                print("=== psutil_pth_debug_checkpoint ===", flush=True)
                process = psutil.Process()
                memory_info = process.memory_info()
                print(f"rss_is_int={isinstance(memory_info.rss, int)}", flush=True)
                print("done", flush=True)
            """
        ),
        encoding="utf-8",
    )

    install = _run(
        [sys.executable, "-m", "retracesoftware", "install"],
        cwd=tmp_path,
        env=os.environ.copy(),
    )
    assert install.returncode == 0, (
        "failed to install pth auto-enable hook\n"
        f"stdout:\n{install.stdout}\n"
        f"stderr:\n{install.stderr}"
    )

    recording_name = "trace.retrace"
    recording = tmp_path / recording_name
    trace_dir = tmp_path / "trace.d"

    record_env = os.environ.copy()
    record_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording_name

    record = _run(
        [sys.executable, script.name],
        cwd=tmp_path,
        env=record_env,
    )
    assert record.returncode == 0, (
        "record failed for pth+debug psutil reproducer\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "rss_is_int=True" in record.stdout
    assert recording.exists()

    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=record_env)
    assert extract.returncode == 0, (
        "extract failed for pth+debug psutil reproducer\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    list_pids = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=record_env,
    )
    assert list_pids.returncode == 0, (
        "list_pids failed for pth+debug psutil reproducer\n"
        f"stdout:\n{list_pids.stdout}\n"
        f"stderr:\n{list_pids.stderr}"
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = trace_dir / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = record_env.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_CONFIG", None)
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "pidfile replay diverged for pth+debug psutil reproducer "
        f"(exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert (
        "was expecting type:retracesoftware.protocol.messages.CheckpointMessage"
        not in combined
    )
    assert "ResultMessage object" not in combined
    assert replay.stdout == record.stdout
