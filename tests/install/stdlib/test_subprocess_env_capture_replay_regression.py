"""Regression coverage for subprocess capture with an explicit child env.

Observed failure:
- Plain Python and Retrace recording both complete.
- Replaying the PidFile diverges at ``_posixsubprocess.fork_exec`` when
  ``subprocess.run`` combines ``capture_output=True``, ``text=True``, and a
  modified ``env`` mapping.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

import pytest


def _run(
    cmd: list[str],
    *,
    env: dict[str, str],
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def test_plain_subprocess_env_capture_control(tmp_path: Path):
    """The reduced subprocess pattern is valid outside Retrace."""

    parent = _write_env_capture_program(tmp_path)
    env = _test_env(tmp_path)

    proc = _run([sys.executable, str(parent)], env=env)

    assert proc.returncode == 0, (
        f"plain run failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert proc.stdout.strip() == "CAPTURED CHILD alpha 7 ERR ok 0"


def test_record_subprocess_env_capture_control(tmp_path: Path):
    """Retrace can record the reduced subprocess env-capture pattern."""

    parent = _write_env_capture_program(tmp_path)
    env = _test_env(tmp_path)
    recording = tmp_path / "subprocess-env-capture.retrace"

    record = _record(parent, recording, env=env)

    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert record.stdout.strip() == "CAPTURED CHILD alpha 7 ERR ok 0"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known replay divergence at _posixsubprocess.fork_exec when "
        "subprocess.run uses capture_output=True, text=True, and env=modified_env."
    ),
)
def test_replay_subprocess_env_capture_matches_record(tmp_path: Path):
    """PidFile replay should match record for captured child output with explicit env."""

    parent = _write_env_capture_program(tmp_path)
    env = _test_env(tmp_path)
    recording = tmp_path / "subprocess-env-capture.retrace"

    record = _record(parent, recording, env=env)
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay_bin = _replay_binary()
    extract = _extract(recording, replay_bin=replay_bin, cwd=tmp_path)
    assert extract.returncode == 0, (
        f"extract failed (exit {extract.returncode})\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    index_file = tmp_path / "subprocess-env-capture.d" / "index.json"
    with index_file.open(encoding="utf-8") as handle:
        root_pid = json.load(handle)["root"]["pid"]

    replay = _run(
        [
            str(replay_bin),
            str(tmp_path / "subprocess-env-capture.d" / f"{root_pid}.bin"),
        ],
        env=env,
    )

    assert replay.returncode == 0, (
        f"replay failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def _write_env_capture_program(tmp_path: Path) -> Path:
    child = tmp_path / "child_env_worker.py"
    child.write_text(
        (
            "import os, sys\n"
            "token = os.environ['RETRACE_CHILD_TOKEN']\n"
            "print(f'CHILD {sys.argv[1]} {token}', flush=True)\n"
            "print('ERR ok', file=sys.stderr, flush=True)\n"
        ),
        encoding="utf-8",
    )

    parent = tmp_path / "parent.py"
    parent.write_text(
        (
            "import os, subprocess, sys\n"
            "child = os.path.join(os.path.dirname(__file__), 'child_env_worker.py')\n"
            "env = os.environ.copy()\n"
            "env['RETRACE_CHILD_TOKEN'] = '7'\n"
            "proc = subprocess.run(\n"
            "    [sys.executable, child, 'alpha'],\n"
            "    capture_output=True,\n"
            "    text=True,\n"
            "    env=env,\n"
            "    check=True,\n"
            ")\n"
            "print('CAPTURED', proc.stdout.strip(), proc.stderr.strip(), proc.returncode, flush=True)\n"
        ),
        encoding="utf-8",
    )
    return parent


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
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            str(script),
        ],
        env=env,
    )


def _replay_binary() -> Path:
    replay_bin = Path(sys.executable).with_name("replay")
    if not replay_bin.exists():
        pytest.skip(f"Go replay binary not installed next to {sys.executable}")
    return replay_bin


def _extract(
    recording: Path,
    *,
    replay_bin: Path,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(replay_bin), "--recording", str(recording), "--extract"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=90,
        env=os.environ.copy(),
    )
