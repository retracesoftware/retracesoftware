"""Regression: executable recordings must work when replay paths contain spaces."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from retracesoftware.replay import binary_path
from tests.helpers import tail


TIMEOUT = 60


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            args,
            127,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}\n",
        )


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    for key in (
        "RETRACE_CONFIG",
        "RETRACE_RECORDING",
        "RETRACE_RECORDING_INODE",
        "RETRACE_REPLAY_BIN",
        "RETRACE_SKIP_CHECKSUMS",
    ):
        env.pop(key, None)
    return env


@pytest.mark.xfail(
    strict=True,
    reason="executable .retrace shebang paths with spaces are split by the OS",
)
def test_executable_recording_supports_replay_binary_path_with_spaces(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()

    script = workspace / "hello.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import tempfile
            import time

            handle = tempfile.NamedTemporaryFile(delete=False)
            try:
                handle.write(b"payload")
                handle.close()
                print("hello", os.path.exists(handle.name), int(time.time()) >= 0)
            finally:
                os.unlink(handle.name)
            """
        ),
        encoding="utf-8",
    )

    replay_dir = workspace / "replay binary parent with spaces"
    replay_dir.mkdir()
    replay_bin = replay_dir / "replay"
    shutil.copy2(binary_path(), replay_bin)
    replay_bin.chmod(0o755)

    recording = workspace / "recording with spaces.retrace"
    env = _clean_env()
    record = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--replay_bin",
            str(replay_bin),
            "--",
            str(script),
        ],
        cwd=workspace,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert recording.exists()
    first_line = recording.read_bytes().splitlines()[0]
    assert b"--recording" in first_line
    assert str(replay_bin).encode("utf-8") in first_line

    extract_dir = workspace / "extracted with spaces"
    extract = _run(
        [str(recording), "--extract", str(extract_dir)],
        cwd=workspace,
        env=env,
    )
    assert extract.returncode == 0, (
        "executable recording failed to launch through its shebang when the "
        "replay binary path contains spaces\n"
        f"stdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
    )
    assert (extract_dir / "index.json").is_file()
