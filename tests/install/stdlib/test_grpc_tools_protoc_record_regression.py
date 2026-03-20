"""Regression: debug recording breaks `python -m grpc_tools.protoc`.

This mirrors the failing part of dockertests `grpc_test` as closely as possible:
- plain run of a script that invokes `python -m grpc_tools.protoc ...` succeeds
- recording the same script with `RETRACE_CONFIG=debug` should also succeed

Current behavior on affected builds:
- record run fails in the child process and protoc reports:
  "Could not make proto path relative: grpc_tools.protoc: No such file or directory"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("grpc_tools.protoc")


def test_record_grpc_tools_protoc_child_process_does_not_fail(tmp_path: Path):
    proto_file = tmp_path / "patient.proto"
    proto_file.write_text(
        (
            'syntax = "proto3";\n'
            "package patient;\n"
            "message PatientRequest { string patient_id = 1; }\n"
            "message PatientResponse { string name = 1; int32 age = 2; string status = 3; }\n"
            "service PatientService {\n"
            "  rpc GetPatientInfo (PatientRequest) returns (PatientResponse);\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    script = tmp_path / "run_protoc.py"
    script.write_text(
        (
            "from pathlib import Path\n"
            "import subprocess\n"
            "import sys\n"
            "test_dir = Path(__file__).resolve().parent\n"
            "subprocess.run(\n"
            "    [\n"
            "        sys.executable,\n"
            "        '-m',\n"
            "        'grpc_tools.protoc',\n"
            "        f'-I{test_dir}',\n"
            "        f'--python_out={test_dir}',\n"
            "        f'--grpc_python_out={test_dir}',\n"
            "        str(test_dir / 'patient.proto'),\n"
            "    ],\n"
            "    check=True,\n"
            "    cwd=test_dir,\n"
            ")\n"
            "print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    plain = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert plain.returncode == 0, (
        f"plain protoc run failed (exit {plain.returncode})\n"
        f"stdout:\n{plain.stdout}\n"
        f"stderr:\n{plain.stderr}"
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

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
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )

    assert proc.returncode == 0, (
        f"recorded protoc run failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
