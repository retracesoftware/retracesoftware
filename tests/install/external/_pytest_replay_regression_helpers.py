"""Shared helpers for pytest record/extract/replay regression tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import textwrap

from tests.helpers import PYTHON, _run_for_pidfile, local_pythonpath, tail


TIMEOUT = 45
REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_TAG = (
    f"cp{sys.version_info.major}{sys.version_info.minor}"
    f"{getattr(sys, 'abiflags', '')}"
)


def minimal_project_pythonpath(tmp_path: Path) -> str:
    """PYTHONPATH for temp-project pytest runs without importing repo tests."""
    paths = [
        str(tmp_path),
        str(REPO_ROOT / "src"),
    ]
    build_root = REPO_ROOT / "build" / BUILD_TAG
    for relpath in (
        Path("cpp") / "functional",
        Path("cpp") / "utils",
        Path("cpp") / "stream",
        Path("cpp") / "cursor",
    ):
        path = build_root / relpath
        if path.exists():
            paths.append(str(path))
    return os.pathsep.join(paths)


def clean_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), local_pythonpath()])
    for key in (
        "RETRACE_CONFIG",
        "RETRACE_RECORDING_INODE",
        "RETRACE_RECORDING",
        "RETRACE_SKIP_CHECKSUMS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")


def record_extract_replay_pytest(
    tmp_path: Path,
    *,
    files: dict[str, str],
    pytest_args: list[str],
    env: dict[str, str] | None = None,
    replay_env: dict[str, str] | None = None,
    stacktraces: bool = True,
    timeout: int = TIMEOUT,
):
    write_files(tmp_path, files)
    recording = tmp_path / "trace.retrace"
    record_env = clean_env(tmp_path, env)

    command = [
        PYTHON,
        "-m",
        "retracesoftware",
        "--recording",
        str(recording),
    ]
    if stacktraces:
        command.append("--stacktraces")
    command.extend(
        [
            "--",
            "-m",
            "pytest",
            *pytest_args,
        ]
    )

    record = _run_for_pidfile(
        command,
        cwd=tmp_path,
        env=record_env,
        timeout=timeout,
    )
    assert recording.exists(), (
        f"recording was not created\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}"
    )

    replay_env = clean_env(tmp_path, replay_env)
    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=replay_env,
        timeout=timeout,
    )
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
    )

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=replay_env,
        timeout=timeout,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay = _run_for_pidfile(
        [str(pidfile)],
        cwd=tmp_path,
        env=replay_env,
        timeout=timeout,
    )
    return record, replay


def assert_successful_replay(record, replay, expected: str) -> None:
    assert replay.returncode == 0, (
        f"pytest replay diverged (exit {replay.returncode})\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    combined = replay.stdout + replay.stderr
    assert expected in combined
    assert "Checkpoint difference:" not in combined
    assert "Could not read:" not in combined
    assert "bind marker returned" not in combined


def assert_replay_does_not_contain_signature(record, replay, *needles: str) -> None:
    combined = replay.stdout + replay.stderr
    assert not all(needle in combined for needle in needles), (
        f"pytest replay hit the documented Retrace signature: {needles!r}\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
