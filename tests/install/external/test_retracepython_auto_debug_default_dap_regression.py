"""Regression for default retracepython auto-debug DAP replay.

This is intentionally opt-in because it needs the dataframe SQL Server example
and its ODBC dependencies. It documents the current failure where the default
``pytest.retrace`` path can make DAP reuse a stale ``pytest.d`` extraction
and then fail inside the Retrace DAP/control replay path instead of stopping
on the target pytest assertion.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

import pytest

from tests.helpers import _completed_process_error, _run_for_pidfile, tail


EXPECTED_FAILURE = "At positional index 249, first diff: 63750.41 != 59463.2"
BIND_MARKER_FAILURE = "bind marker returned when bind was expected"


def _copy_project(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".git",
        ".venv*",
        ".pytest_cache",
        "*.ai-report.*",
        "*.retrace",
        "*.d",
        "__pycache__",
    )
    shutil.copytree(src, dst, ignore=ignore)


def _record_pytest(workdir: Path) -> Path:
    true_executable = shutil.which("true") or "/usr/bin/true"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONFAULTHANDLER": "1",
            "PYTHONPATH": str(workdir),
            "RETRACE_AUTO_DEBUG": "1",
            "RETRACE_AI_DRIVER_COMMAND": true_executable,
        }
    )
    env.pop("RETRACE_AUTO_DEBUG_SUPERVISED", None)
    env.pop("RETRACE_RECORDING", None)
    env.pop("RETRACE_CONFIG", None)

    cmd = [
        sys.executable,
        "-m",
        "retracesoftware.retracepython",
        "-m",
        "pytest",
        "-vs",
        "tests",
    ]

    proc = _run_for_pidfile(cmd, cwd=workdir, env=env, timeout=180)
    assert proc.returncode == 1, _completed_process_error(
        "record dataframe pytest failure",
        proc,
    )
    assert EXPECTED_FAILURE in proc.stdout + proc.stderr, (
        f"target pytest failure changed\nstdout:\n{tail(proc.stdout)}\n"
        f"stderr:\n{tail(proc.stderr)}"
    )
    path = workdir / "pytest.retrace"
    assert path.exists(), f"recording not created: {path}"
    return path


def _extract_dir(recording: Path) -> Path:
    return recording.with_suffix(".d")


def _extract_recording(recording: Path, workdir: Path) -> None:
    proc = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=workdir,
        env=os.environ.copy(),
        timeout=90,
    )
    assert proc.returncode == 0, _completed_process_error("extract recording", proc)
    assert _extract_dir(recording).exists()


def _dap_continue_description(recording: Path, workdir: Path) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workdir)

    script = """
import json
import sys
from retracesoftware.ai_driver import DAPExecutor

executor = DAPExecutor(sys.argv[1])
try:
    start = executor.execute("start_replay_session", {})
    if not start.get("ok"):
        print(json.dumps({"error": start}, sort_keys=True))
        raise SystemExit(2)
    cont = executor.execute("continue_execution", {})
    if not cont.get("ok"):
        print(json.dumps({"error": cont}, sort_keys=True))
        raise SystemExit(3)
    stop = (cont.get("data") or {}).get("stop") or {}
    print(json.dumps({"description": str(stop.get("description") or "")}, sort_keys=True))
finally:
    executor.close()
"""
    proc = _run_for_pidfile(
        [sys.executable, "-c", script, str(recording)],
        cwd=workdir,
        env=env,
        timeout=90,
    )
    assert proc.returncode == 0, _completed_process_error("dap continue", proc)
    import json

    return str(json.loads(proc.stdout)["description"])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "default pytest.retrace auto-debug DAP can reuse stale pytest.d "
        "extraction state and fail inside Retrace's DAP/control replay "
        "path instead of stopping on the target pytest failure"
    ),
)
def test_default_pytest_retrace_dap_refreshes_stale_extraction(
    tmp_path: Path,
) -> None:
    if os.environ.get("RETRACE_RUN_DATAFRAME_DAP_E2E") != "1":
        pytest.skip(
            "set RETRACE_RUN_DATAFRAME_DAP_E2E=1 and "
            "RETRACE_DATAFRAME_TEST_EXAMPLE=/path/to/dataframe-test-example"
        )
    source = Path(os.environ["RETRACE_DATAFRAME_TEST_EXAMPLE"]).resolve()
    if not (source / "tests" / "test_financial_report.py").exists():
        pytest.skip(f"dataframe test example not found: {source}")

    workdir = tmp_path / "default"
    _copy_project(source, workdir)

    recording = _record_pytest(workdir)
    _extract_recording(recording, workdir)

    second_recording = _record_pytest(workdir)
    assert second_recording == recording
    assert _extract_dir(second_recording).exists()

    second_dap = _dap_continue_description(second_recording, workdir)

    assert EXPECTED_FAILURE in second_dap
    assert BIND_MARKER_FAILURE not in second_dap
