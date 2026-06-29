"""Regression: Retrace venv child Python processes must not corrupt shared traces.

The public always-on venv flow is:

    python -m retracesoftware venv .venv
    RETRACE_RECORDING=trace.retrace .venv/bin/python -m pytest tests

When pytest starts a child Python subprocess, that child inherits
``RETRACE_RECORDING`` and runs through the venv's Retrace-aware Python wrapper.
The child must append its process stream to the shared trace without
re-preparing/truncating the executable trace header written by the parent.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import textwrap

from tests.helpers import tail


TIMEOUT = 60
def _run(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )


def _clean_retrace_env() -> dict[str, str]:
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    for key in (
        "RETRACE_CONFIG",
        "RETRACE_RECORDING",
        "RETRACE_RECORDING_INODE",
        "RETRACE_SKIP_CHECKSUMS",
    ):
        env.pop(key, None)
    return env


def _prepend_pythonpath(env: dict[str, str], *paths: str) -> None:
    existing = env.get("PYTHONPATH")
    parts = [path for path in paths if path]
    if existing:
        parts.append(existing)
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)


def _test_runner_site_packages() -> str:
    return sysconfig.get_paths()["purelib"]


def test_retrace_venv_pytest_child_python_process_extracts_without_trace_header_corruption(
    tmp_path: Path,
) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_child_python.py").write_text(
        textwrap.dedent(
            """
            import subprocess
            import sys


            def test_child_python_subprocess():
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os, time; "
                            "print('CHILD-PYTHON', os.getpid(), int(time.time()) >= 0)"
                        ),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                assert "CHILD-PYTHON" in proc.stdout
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    install_env = _clean_retrace_env()
    venv_dir = tmp_path / ".retrace-venv"
    install = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "venv",
            str(venv_dir),
            "--without-pip",
            "--system-site-packages",
        ],
        cwd=tmp_path,
        env=install_env,
    )
    assert install.returncode == 0, (
        f"create retrace venv failed\nstdout:\n{tail(install.stdout)}\n"
        f"stderr:\n{tail(install.stderr)}"
    )
    retrace_python = venv_dir / "bin" / "python"

    record_env = install_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording.name
    _prepend_pythonpath(record_env, _test_runner_site_packages())

    record = _run(
        [str(retrace_python), "-m", "pytest", "tests", "-q", "--tb=short"],
        cwd=tmp_path,
        env=record_env,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert "1 passed" in record.stdout
    assert recording.exists()
    assert b"--recording" in recording.read_bytes().splitlines()[0]

    inspect_env = install_env.copy()
    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=inspect_env)
    assert extract.returncode == 0, (
        "extract failed; child Python launcher likely rewrote/truncated the "
        "shared trace header\n"
        f"stdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
    )
    combined_extract = extract.stdout + extract.stderr
    assert "parse preamble" not in combined_extract

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
        env=inspect_env,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\nstderr:\n{tail(list_pids.stderr)}"
    )
    pids = [line for line in list_pids.stdout.splitlines() if line.strip()]
    assert len(pids) >= 2


def test_retrace_venv_child_python_process_root_pidfile_replays(
    tmp_path: Path,
) -> None:
    """Root replay must preserve the wrapper-backed sys.executable branch.

    This is the reduced shape behind issue #64.  The parent process runs under
    a Retrace-created venv, so record sees ``sys.executable`` as the venv
    wrapper.  Replaying the extracted root pidfile must keep that same logical
    value; otherwise the child ``fork_exec`` checkpoint diverges before the
    recorded subprocess result is consumed.
    """

    parent = tmp_path / "parent.py"
    parent.write_text(
        textwrap.dedent(
            """
            import subprocess
            import sys


            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; print('child-pid', os.getpid())",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            assert "child-pid" in proc.stdout
            print("parent ok", flush=True)
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    install_env = _clean_retrace_env()
    venv_dir = tmp_path / ".retrace-venv"
    install = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "venv",
            str(venv_dir),
            "--without-pip",
            "--system-site-packages",
        ],
        cwd=tmp_path,
        env=install_env,
    )
    assert install.returncode == 0, (
        f"create retrace venv failed\nstdout:\n{tail(install.stdout)}\n"
        f"stderr:\n{tail(install.stderr)}"
    )
    retrace_python = venv_dir / "bin" / "python"

    record_env = install_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording.name

    record = _run([str(retrace_python), parent.name], cwd=tmp_path, env=record_env)
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert record.stdout == "parent ok\n"

    inspect_env = install_env.copy()
    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=inspect_env)
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
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
        env=inspect_env,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\nstderr:\n{tail(list_pids.stderr)}"
    )
    pids = [line for line in list_pids.stdout.splitlines() if line.strip()]
    assert len(pids) >= 2

    root_pid = pids[0]
    root_pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert root_pidfile.exists()

    replay = _run([str(root_pidfile)], cwd=tmp_path, env=inspect_env)
    combined_replay = replay.stdout + replay.stderr
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\n"
        f"stderr:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
    assert "Checkpoint difference:" not in combined_replay
