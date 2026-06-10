"""Regression coverage for passthrough file descriptors used by fd syscalls."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _prepend_env_path(env: dict[str, str], key: str, paths: list[Path]) -> None:
    prefix = os.pathsep.join(str(path) for path in paths if path.exists())
    if not prefix:
        return
    existing = env.get(key)
    env[key] = f"{prefix}{os.pathsep}{existing}" if existing else prefix


def _use_this_checkout(env: dict[str, str]) -> None:
    build_tag = f"cp{sys.version_info.major}{sys.version_info.minor}{getattr(sys, 'abiflags', '')}"
    build_dir = _REPO_ROOT / "build" / build_tag
    build_paths = [
        build_dir / "cpp" / "functional",
        build_dir / "cpp" / "utils",
        build_dir / "cpp" / "stream",
        build_dir / "cpp" / "cursor",
    ]
    _prepend_env_path(env, "PYTHONPATH", [_REPO_ROOT / "src", *build_paths])
    _prepend_env_path(env, "MESONPY_EDITABLE_SKIP", [build_dir])


def _run(args, *, cwd: Path, env: dict[str, str], timeout: int = 90):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_tempfile_gettempdir_passthrough_fd_probe_replays(tmp_path: Path) -> None:
    script = tmp_path / "fd_provenance_tempfile.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import tempfile


            untraced_tmp = os.path.abspath("untraced_tmp")
            os.makedirs(untraced_tmp, exist_ok=True)
            os.environ["TMPDIR"] = untraced_tmp

            keep = [
                os.open(f"/tmp/retrace-fd-drift-{os.getpid()}-{i}", os.O_CREAT | os.O_RDWR, 0o600)
                for i in range(2)
            ]

            tempfile.tempdir = None
            tempfile._get_candidate_names = lambda: iter(["retraceprobe"])
            tempdir = tempfile.gettempdir()

            assert tempdir == untraced_tmp
            print(f"fd provenance tempfile ok {keep} {tempdir}", flush=True)
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    _use_this_checkout(env)

    record = _run(
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
        cwd=tmp_path,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{record.stdout}\nstderr:\n{record.stderr}"
    )

    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=env)
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{extract.stdout}\nstderr:\n{extract.stderr}"
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
        env=env,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{list_pids.stdout}\nstderr:\n{list_pids.stderr}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run([str(tmp_path / "trace.d" / f"{root_pid}.bin")], cwd=tmp_path, env=env)
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{replay.stdout}\nstderr:\n{replay.stderr}"
    )
    assert "fd provenance tempfile ok" in replay.stdout
