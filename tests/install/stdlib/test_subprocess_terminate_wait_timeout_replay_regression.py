"""Regression coverage for ``Popen.terminate(); Popen.wait(timeout=...)``.

Observed failure:
- Recording succeeds and the child process exits after SIGTERM.
- Replaying the extracted PidFile skips the recorded ``posix.kill`` call from
  ``Popen.terminate()``.
- The next replayed call is ``time.monotonic()`` from ``Popen.wait(timeout=5)``,
  which consumes the recorded ``posix.kill`` result (``None``) and crashes with
  ``TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from tests.helpers import PYTHON


def _run(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None = None,
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


def test_plain_popen_terminate_wait_timeout_control(tmp_path: Path):
    """The reduced subprocess shutdown pattern is valid outside Retrace."""

    script = _write_reproducer(tmp_path)
    env = _test_env(tmp_path)

    proc = _run([sys.executable, str(script)], cwd=tmp_path, env=env)

    assert proc.returncode == 0, (
        f"plain run failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "=== popen_terminate_wait_timeout ===" in proc.stdout
    assert "wait -15" in proc.stdout


def test_record_popen_terminate_wait_timeout_control(tmp_path: Path):
    """Retrace can record the reduced subprocess shutdown pattern."""

    script = _write_reproducer(tmp_path)
    env = _test_env(tmp_path)
    recording = tmp_path / "popen-terminate-wait.retrace"

    record = _record(script, recording, env=env)

    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "wait -15" in record.stdout


def test_replay_popen_terminate_wait_timeout_matches_record(tmp_path: Path):
    """Executable PidFile replay should match record for terminate+wait."""

    script = _write_reproducer(tmp_path)
    env = _test_env(tmp_path)
    recording = tmp_path / "test.retrace"

    record = _record(script, recording, env=env)
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    extract = _extract(recording, cwd=tmp_path, env=env)
    assert extract.returncode == 0, (
        f"extract failed (exit {extract.returncode})\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    list_pids = _list_pids(recording, cwd=tmp_path, env=env)
    assert list_pids.returncode == 0, (
        f"list_pids failed (exit {list_pids.returncode})\n"
        f"stdout:\n{list_pids.stdout}\n"
        f"stderr:\n{list_pids.stderr}"
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "test.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)

    assert replay.returncode == 0, (
        f"replay failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def _write_reproducer(tmp_path: Path) -> Path:
    script = tmp_path / "popen_terminate_wait_timeout.py"
    script.write_text(
        (
            "import subprocess\n"
            "import time\n"
            "\n"
            "print('=== popen_terminate_wait_timeout ===', flush=True)\n"
            "proc = subprocess.Popen(['/bin/sleep', '30'])\n"
            "print('pid', proc.pid, flush=True)\n"
            "time.sleep(0.05)\n"
            "proc.terminate()\n"
            "print('wait', proc.wait(timeout=5), flush=True)\n"
        ),
        encoding="utf-8",
    )
    return script


def _test_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["PYTHONPATH"] = (
        f"{tmp_path}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(tmp_path)
    )
    return env


def _record(
    script: Path,
    recording: Path,
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--",
            script.name,
        ],
        cwd=script.parent,
        env=env,
    )


def _extract(
    recording: Path,
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run([str(recording), "--extract"], cwd=cwd, env=env)


def _list_pids(
    recording: Path,
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=cwd,
        env=env,
    )
