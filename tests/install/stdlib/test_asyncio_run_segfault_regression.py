"""Regression: `_socket` proxying (`socket` + `socketpair`) segfaults asyncio record.

Root component identified by bisect:
- Removing `_socket` config from install patching avoids the crash.
- Keeping only `_socket` proxy = ["socket", "socketpair"] reproduces it.

So this test isolates to a single retrace component path:
`install` module patching for stdlib `_socket`.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("config_name", ["release", "debug"])
def test_record_minimal_asyncio_run_does_not_segfault(tmp_path: Path, config_name: str):
    script = tmp_path / "mini_asyncio.py"
    script.write_text(
        (
            "import asyncio\n"
            "async def main():\n"
            "    await asyncio.sleep(0.01)\n"
            "asyncio.run(main())\n"
            "print('ok')\n"
        ),
        encoding="utf-8",
    )

    # Isolate to just the `_socket` install config that reproduces the crash.
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "_socket.toml").write_text(
        'proxy = ["socket", "socketpair"]\n',
        encoding="utf-8",
    )

    recording = tmp_path / f"{config_name}.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = config_name
    env["RETRACE_MODULES_PATH"] = str(modules_dir)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--raw",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        f"record failed for config={config_name} (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
