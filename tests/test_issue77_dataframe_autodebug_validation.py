"""End-to-end validation for GitHub issue #77 dataframe-style pytest auto-debug."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from retracesoftware.ai_driver import (
    _pytest_failure_hint,
    _pytest_failure_hint_from_output,
)
from retracesoftware.replay import binary_path as replay_binary_path
from tests.helpers_ai_driver_e2e import assert_prepositioned_application_stack


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issue77_dataframe_style"
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIFF = "63750.41 != 59463.2"


def _replay_bin() -> str:
    return replay_binary_path()


def _python() -> str:
    return sys.executable


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(FIXTURE), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["RETRACE_REPLAY_BIN"] = _replay_bin()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if extra:
        env.update(extra)
    return env


def _record_pytest_failure(trace_path: Path) -> str:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _python(),
        "-m",
        "retracesoftware",
        "--recording",
        str(trace_path),
        "--",
        "-m",
        "pytest",
        "tests/test_financial_report.py",
        "-q",
    ]
    completed = subprocess.run(
        cmd,
        cwd=FIXTURE,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    assert completed.returncode != 0, f"expected pytest failure, got success\n{output}"
    assert EXPECTED_DIFF in output, output
    assert "tests/test_financial_report.py::test_generate_financial_report" in output
    assert trace_path.exists() and trace_path.stat().st_size > 0
    return output


@pytest.fixture(scope="module")
def issue77_trace(tmp_path_factory) -> tuple[Path, str]:
    trace_dir = tmp_path_factory.mktemp("issue77")
    trace_path = trace_dir / "dataframe-style.retrace"
    output = _record_pytest_failure(trace_path)
    return trace_path, output


def test_issue77_prepositioning_returns_application_stack(issue77_trace):
    trace_path, _ = issue77_trace
    assert_prepositioned_application_stack(
        trace_path=str(trace_path),
        task="-m pytest tests/test_financial_report.py -q",
        path_substring="tests/test_financial_report.py",
    )


def test_issue77_recorded_pytest_output_is_parseable(issue77_trace):
    trace_path, live_output = issue77_trace

    hint = _pytest_failure_hint_from_output(live_output, cwd=FIXTURE)
    assert hint is not None
    assert hint["function"] == "test_generate_financial_report"
    assert hint["exception_type"] == "AssertionError"
    assert EXPECTED_DIFF in hint["exception_message"]
    assert hint["classification"] == "application"
    assert hint["filename"].endswith("tests/test_financial_report.py")

    replay_hint = _pytest_failure_hint(str(trace_path))
    assert replay_hint is not None
    assert replay_hint["function"] == "test_generate_financial_report"
    assert replay_hint["classification"] == "application"
