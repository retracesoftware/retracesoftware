"""Regression: recorded child `python -m module` gets incorrect argv.

Observed user-facing failure:
- `dockertests/tests/grpc_test/test.py` passes normally
- with `RETRACE_CONFIG=debug`, record fails while running
  `python -m grpc_tools.protoc ...`
- protoc reports:
  `Could not make proto path relative: grpc_tools.protoc: No such file or directory`

Root component:
- `retracesoftware.run.run_python_command` handling of `-m` execution in
  auto-enabled child processes.
- It sets `sys.argv` in a way that injects module metadata into user args,
  which breaks module CLIs that parse argv positionally.

This test isolates that component behavior using a tiny synthetic module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_record_child_python_m_preserves_module_argv_shape(tmp_path: Path):
    module_file = tmp_path / "argvprobe.py"
    argv_capture = tmp_path / "child_argv.json"
    module_file.write_text(
        (
            "import json, os, sys\n"
            "capture = os.environ['ARGV_CAPTURE']\n"
            "with open(capture, 'w', encoding='utf-8') as f:\n"
            "    json.dump(sys.argv, f)\n"
            "if len(sys.argv) < 2 or sys.argv[1] != 'OK':\n"
            "    raise SystemExit(f'BAD_ARGV:{sys.argv!r}')\n"
            "print('child-ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        (
            "import os, subprocess, sys\n"
            "env = os.environ.copy()\n"
            "subprocess.run([sys.executable, '-m', 'argvprobe', 'OK'], check=True, env=env)\n"
            "print('parent-ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    common_env = os.environ.copy()
    existing_pythonpath = common_env.get("PYTHONPATH")
    common_env["PYTHONPATH"] = (
        f"{tmp_path}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(tmp_path)
    )
    common_env["ARGV_CAPTURE"] = str(argv_capture)

    # Control: plain Python should pass.
    plain = subprocess.run(
        [sys.executable, str(parent_script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=common_env,
    )
    assert plain.returncode == 0, (
        f"plain run failed (exit {plain.returncode})\n"
        f"stdout:\n{plain.stdout}\n"
        f"stderr:\n{plain.stderr}"
    )

    recording = tmp_path / "trace.retrace"
    record_env = common_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--",
            str(parent_script),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=record_env,
    )

    captured_argv = None
    if argv_capture.exists():
        captured_argv = json.loads(argv_capture.read_text(encoding="utf-8"))

    assert proc.returncode == 0, (
        f"record run failed (exit {proc.returncode})\n"
        f"captured child argv: {captured_argv}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
