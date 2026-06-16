"""Regression coverage for pwd.struct_passwd replay as a tuple-like value."""

from __future__ import annotations

import os
from pathlib import Path

from tests.helpers import PYTHON, _run_for_pidfile, local_pythonpath, tail


def test_pwd_struct_passwd_indexing_replays_when_user_env_is_absent(
    tmp_path: Path,
) -> None:
    script = tmp_path / "pwd_struct_passwd_repro.py"
    script.write_text(
        """
import getpass
import os
import pwd

for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
    os.environ.pop(name, None)

entry = pwd.getpwuid(os.getuid())
print(f"pwd-name={entry[0]} attr={entry.pw_name} getuser={getpass.getuser()}")
assert entry[0] == entry.pw_name == getpass.getuser()
        """,
        encoding="utf-8",
    )
    recording = tmp_path / "trace.bin"

    env = os.environ.copy()
    env["PYTHONPATH"] = local_pythonpath()
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        env.pop(name, None)

    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--stacktraces",
            "--",
            str(script),
        ],
        cwd=tmp_path,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )

    replay = _run_for_pidfile(
        [PYTHON, "-m", "retracesoftware", "--recording", str(recording)],
        cwd=tmp_path,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\n"
        f"stderr:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
