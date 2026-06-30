"""End-to-end validation for GitHub issue #75 auto-debug application frames."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from retracesoftware.ai_driver import (
    DAPExecutor,
    _initial_observation,
    _pytest_failure_hint,
    _pytest_failure_hint_from_output,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issue75_period_rates"
REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_BIN = os.environ.get("RETRACE_REPLAY_BIN") or str(REPO_ROOT / ".retrace-replay-bin")


def _python() -> str:
    return sys.executable


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(FIXTURE), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["RETRACE_REPLAY_BIN"] = REPLAY_BIN
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
        "unit_tests/test_period_rates.py",
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
    assert "0.87905" in output or "0.819934" in output, output
    assert trace_path.exists() and trace_path.stat().st_size > 0
    return output


@pytest.fixture(scope="module")
def issue75_trace(tmp_path_factory) -> tuple[Path, str]:
    trace_dir = tmp_path_factory.mktemp("issue75")
    trace_path = trace_dir / "unit-period-rates.retrace"
    output = _record_pytest_failure(trace_path)
    return trace_path, output


def test_issue75_recorded_pytest_output_is_parseable(issue75_trace):
    trace_path, live_output = issue75_trace
    hint = _pytest_failure_hint_from_output(live_output, cwd=FIXTURE)
    assert hint is not None
    assert "test_period_rates" in hint["function"]
    assert hint["exception_type"] == "AssertionError"
    assert hint["line"] > 0
    assert str(FIXTURE) in hint["filename"] or hint["filename"].endswith(
        "unit_tests/test_period_rates.py"
    )

    replay_hint = _pytest_failure_hint(str(trace_path), replay_bin=REPLAY_BIN)
    assert replay_hint is not None
    assert replay_hint["line"] > 0


def test_issue75_prepositioning_returns_application_stack(issue75_trace):
    trace_path, _ = issue75_trace
    executor = DAPExecutor(str(trace_path), REPLAY_BIN)
    transcript: list[dict] = []
    observation = _initial_observation(
        type(
            "Args",
            (),
            {
                "task": "-m pytest unit_tests/test_period_rates.py -q",
                "trace": str(trace_path),
            },
        )(),
        executor,
        transcript,
    )

    assert "pre-positioned" in observation["summary"].lower() or observation.get("tool_result")
    assert transcript, "expected prelude transcript from _prime_pytest_failure_breakpoint"

    stack_step = next(item for item in reversed(transcript) if item["tool"] == "get_stack_trace")
    result = stack_step["result"]
    assert result.get("ok") is True, json.dumps(result, indent=2)

    frames = result.get("data", {}).get("stack_frames", [])
    assert frames, f"expected application frames, got {result!r}"

    paths = [
        frame.get("source", {}).get("path", "")
        for frame in frames
        if isinstance(frame, dict)
    ]
    assert any("unit_tests/test_period_rates.py" in path for path in paths), paths
    assert not any("_pytest" in path for path in paths), paths
    assert "Unable to Retrieve Application Context" not in json.dumps(result)

    executor.close()


def test_issue75_bad_pytest_internal_stop_is_not_swallowed_as_empty_stack(issue75_trace):
    """Reproduce issue comment DAP sequence: pytest-internal BP must not fake empty stack."""
    trace_path, _ = issue75_trace
    replay = REPLAY_BIN
    extract = subprocess.run(
        [replay, "--recording", str(trace_path), "--extract"],
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert extract.returncode == 0, extract.stderr

    index_path = trace_path.with_suffix(".d") / "index.json"
    pid = json.loads(index_path.read_text(encoding="utf-8"))["root"]["pid"]
    pidfile = index_path.parent / f"{pid}.bin"

    pytest_config = None
    for path in Path(sys.prefix).rglob("_pytest/config/__init__.py"):
        if path.is_file():
            pytest_config = path
            break
    if pytest_config is None:
        pytest.skip("could not locate _pytest/config/__init__.py")

    script = REPO_ROOT / "tests/scripts/issue75_dap_probe.py"
    completed = subprocess.run(
        [
            _python(),
            str(script),
            str(pidfile),
            str(pytest_config),
            "2023",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    if payload.get("postContinueEvent") == "terminated":
        pytest.skip(
            "pytest config line 2023 is not executed in the minimal issue75 fixture; "
            "not_stopped propagation is covered by Go TestProxyStackTraceDoesNotHideNotStoppedControlError"
        )
    stack = payload["stackTrace"]
    # Before the fix, not_stopped became success=true with empty frames.
    # After the Python frame-preservation fix, this stop may be inspectable.
    # After the Go fix, not_stopped must never be success=true with empty frames.
    if stack["success"] is True:
        assert stack["frame_count"] > 0 or stack.get("message"), payload
    else:
        assert stack.get("retrace", {}).get("category") == "inspection_unavailable", payload
