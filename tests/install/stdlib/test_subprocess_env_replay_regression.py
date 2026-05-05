"""Regression coverage for replay environment restoration."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _ordered_env(reverse: bool = False) -> dict[str, str]:
    return {key: os.environ[key] for key in sorted(os.environ, reverse=reverse)}


def _prepend_env_path(env: dict[str, str], key: str, paths: list[Path]) -> None:
    prefix = os.pathsep.join(str(path) for path in paths if path.exists())
    if not prefix:
        return
    existing = env.get(key)
    env[key] = f"{prefix}{os.pathsep}{existing}" if existing else prefix


def _active_mesonpy_build_dirs() -> list[Path]:
    paths = []
    for finder in sys.meta_path:
        build_path = getattr(finder, "_build_path", None)
        if build_path is not None and getattr(finder, "_name", None) == "retracesoftware":
            paths.append(Path(build_path))
    return paths


def _use_this_checkout(env: dict[str, str]) -> None:
    build_tag = f"cp{sys.version_info.major}{sys.version_info.minor}{getattr(sys, 'abiflags', '')}"
    build_dirs = [_REPO_ROOT / "build" / build_tag, *_active_mesonpy_build_dirs()]
    build_paths = []
    for build_dir in build_dirs:
        build_paths.extend(
            [
                build_dir / "cpp" / "functional",
                build_dir / "cpp" / "utils",
                build_dir / "cpp" / "stream",
                build_dir / "cpp" / "cursor",
            ]
        )
    _prepend_env_path(env, "PYTHONPATH", [_REPO_ROOT / "src", *build_paths])
    _prepend_env_path(env, "MESONPY_EDITABLE_SKIP", build_dirs)


def test_replay_restores_recorded_environment_before_subprocess_env_copy(tmp_path: Path):
    child = tmp_path / "child_env_worker.py"
    child.write_text(
        (
            "import os, sys\n"
            "print(f\"CHILD {sys.argv[1]} {os.environ['RETRACE_CHILD_MULTIPLIER']}\")\n"
            "print('ERR ok', file=sys.stderr)\n"
        ),
        encoding="utf-8",
    )

    parent = tmp_path / "parent.py"
    parent.write_text(
        (
            "import os, subprocess, sys\n"
            "env = os.environ.copy()\n"
            "env['RETRACE_CHILD_MULTIPLIER'] = '7'\n"
            "proc = subprocess.run(\n"
            "    [sys.executable, 'child_env_worker.py', 'alpha'],\n"
            "    check=True,\n"
            "    capture_output=True,\n"
            "    text=True,\n"
            "    env=env,\n"
            ")\n"
            "print('CAPTURED', proc.stdout.strip(), proc.stderr.strip(), proc.returncode)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    record_env = _ordered_env()
    replay_env = _ordered_env(reverse=True)
    _use_this_checkout(record_env)
    _use_this_checkout(replay_env)
    replay_env["REPLAY_ONLY_ENV_SHOULD_NOT_LEAK"] = "1"

    record = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--stacktraces",
            "--",
            str(parent),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
        env=record_env,
    )
    assert record.returncode == 0, (
        f"record run failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
        env=replay_env,
    )
    assert replay.returncode == 0, (
        f"replay run failed (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout == "CAPTURED CHILD alpha 7 ERR ok 0\n"
