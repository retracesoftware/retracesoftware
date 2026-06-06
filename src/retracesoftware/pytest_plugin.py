"""Opt-in pytest integration for failed-test Retrace run artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from retracesoftware.pytest_runs import (
    build_failed_test_manifest,
    create_run_id,
    write_manifest,
)

RETRACE_PYTEST_RECORDING_CHILD = "RETRACE_PYTEST_RECORDING_CHILD"
EXISTING_CLI_CAPTURE_METHOD = "existing-cli-subprocess"


@dataclass(frozen=True)
class RecordingCaptureResult:
    available: bool
    placeholder: bool
    capture_method: str
    failure_reason: str | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("retrace")
    group.addoption(
        "--retrace",
        action="store_true",
        default=False,
        help="Create Retrace failed-test artifacts for failing tests.",
    )
    group.addoption(
        "--retrace-output-dir",
        default=str(Path(".retrace") / "runs"),
        help="Directory where Retrace failed-test run artifacts are written.",
    )
    group.addoption(
        "--retrace-mode",
        default="failed-only",
        help="Retrace pytest capture mode. Currently only 'failed-only' is implemented.",
    )
    group.addoption(
        "--retrace-max-runs",
        type=int,
        default=None,
        help="Maximum retained failed-test runs. Accepted for future retention support.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get(RETRACE_PYTEST_RECORDING_CHILD):
        return
    if not config.getoption("retrace", default=False):
        return
    mode = config.getoption("retrace_mode")
    if mode != "failed-only":
        raise pytest.UsageError(f"unsupported --retrace-mode={mode!r}; only 'failed-only' is implemented")
    config._retrace_recorded_failures = set()  # type: ignore[attr-defined]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    report = outcome.get_result()
    config = item.config
    if os.environ.get(RETRACE_PYTEST_RECORDING_CHILD):
        return
    if not config.getoption("retrace", default=False):
        return
    if not report.failed:
        return

    recorded = getattr(config, "_retrace_recorded_failures", set())
    if item.nodeid in recorded:
        return
    recorded.add(item.nodeid)
    config._retrace_recorded_failures = recorded  # type: ignore[attr-defined]

    manifest = _write_failed_test_artifacts(item, call, report)
    _emit_next_steps(config, manifest)


def _write_failed_test_artifacts(
    item: pytest.Item,
    call: pytest.CallInfo[Any],
    report: pytest.TestReport,
) -> dict[str, Any]:
    config = item.config
    run_id = create_run_id()
    output_dir = Path(config.getoption("retrace_output_dir"))
    run_dir = output_dir / run_id
    recording_path = run_dir / "recording.bin"
    failure_path = run_dir / "failure.txt"
    manifest_path = run_dir / "manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)

    exception_type, exception_message = _exception_details(call, report)
    traceback_summary = _traceback_summary(report)
    active_plugins = _active_plugin_names(config)
    env_var_names = sorted(os.environ)
    test_file, test_line, test_function = _item_location(item)
    capture = _capture_failed_test_recording(recording_path, item)

    manifest = build_failed_test_manifest(
        run_id=run_id,
        recording_path=recording_path,
        manifest_path=manifest_path,
        failure_path=failure_path,
        node_id=item.nodeid,
        test_file=test_file,
        test_line=test_line,
        test_function=test_function,
        pytest_version=pytest.__version__,
        active_plugins=active_plugins,
        randomly_detected=_plugin_detected(active_plugins, "randomly"),
        randomly_seed=_pytest_randomly_seed(config),
        env_detected=_plugin_detected(active_plugins, "env", "pytest-env", "pytest_env"),
        sugar_detected=_plugin_detected(active_plugins, "sugar", "pytest-sugar"),
        teamcity_messages_detected=_plugin_detected(
            active_plugins,
            "teamcity",
            "teamcity-messages",
            "pytest-teamcity",
        ),
        exception_type=exception_type,
        exception_message=exception_message,
        traceback_summary=traceback_summary,
        recording_placeholder=capture.placeholder,
        recording_capture_method=capture.capture_method,
        recording_available=capture.available,
        recording_failure_reason=capture.failure_reason,
        cwd=Path.cwd(),
        coverage_detected=_coverage_detected(active_plugins),
        teamcity_detected="TEAMCITY_VERSION" in os.environ,
        ci_detected=_ci_detected(),
        env_var_names=env_var_names,
    )

    written_manifest_path = write_manifest(manifest, runs_dir=output_dir)
    written_manifest = {
        **manifest,
        "manifest_path": str(written_manifest_path),
    }
    failure_path.write_text(_render_failure_txt(written_manifest), encoding="utf-8")
    return {
        **written_manifest,
        "manifest_path": str(written_manifest_path),
    }


def _capture_failed_test_recording(recording_path: Path, item: pytest.Item) -> RecordingCaptureResult:
    """Record the failed node through the existing process-level CLI recorder."""

    command = [
        sys.executable,
        "-m",
        "retracesoftware",
        "--recording",
        str(recording_path),
        "--format",
        "binary",
        "--stacktraces",
        "--",
        "-m",
        "pytest",
        item.nodeid,
    ]
    env = dict(os.environ)
    env[RETRACE_PYTEST_RECORDING_CHILD] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return RecordingCaptureResult(
            available=False,
            placeholder=False,
            capture_method=EXISTING_CLI_CAPTURE_METHOD,
            failure_reason=f"recording command could not start: {exc}",
        )

    recording_exists = recording_path.exists() and recording_path.stat().st_size > 0
    if recording_exists and result.returncode != 0:
        return RecordingCaptureResult(
            available=True,
            placeholder=False,
            capture_method=EXISTING_CLI_CAPTURE_METHOD,
        )
    if recording_exists:
        reason = "recorded pytest node passed unexpectedly; no failed-test recording was captured"
    else:
        reason = _summarize_recording_failure(result)
    return RecordingCaptureResult(
        available=False,
        placeholder=False,
        capture_method=EXISTING_CLI_CAPTURE_METHOD,
        failure_reason=reason,
    )


def _summarize_recording_failure(result: subprocess.CompletedProcess[str]) -> str:
    for stream_name, text in (("stderr", result.stderr), ("stdout", result.stdout)):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return f"recording command exited {result.returncode}; first {stream_name}: {lines[0][:400]}"
    return f"recording command exited {result.returncode} without creating recording"


def _item_location(item: pytest.Item) -> tuple[str, int | None, str]:
    test_file, line_number, test_name = item.location
    return test_file, line_number + 1 if line_number is not None else None, test_name or item.name


def _exception_details(call: pytest.CallInfo[Any], report: pytest.TestReport) -> tuple[str, str]:
    if call.excinfo is not None:
        return call.excinfo.typename, str(call.excinfo.value)
    longrepr = getattr(report, "longreprtext", "") or ""
    lines = [line.strip() for line in longrepr.splitlines() if line.strip()]
    if lines:
        return "Failure", lines[-1][:500]
    return "Failure", ""


def _traceback_summary(report: pytest.TestReport) -> str:
    longrepr = getattr(report, "longreprtext", "") or ""
    lines = [line.rstrip() for line in longrepr.splitlines() if line.strip()]
    if len(lines) > 12:
        lines = [*lines[:8], "...", *lines[-3:]]
    return "\n".join(lines)[:2000]


def _active_plugin_names(config: pytest.Config) -> list[str]:
    names = []
    for name, plugin in config.pluginmanager.list_name_plugin():
        if name and plugin is not None:
            names.append(str(name))
    return sorted(set(names))


def _plugin_detected(plugin_names: list[str], *needles: str) -> bool:
    normalized_names = {name.lower().replace("_", "-") for name in plugin_names}
    normalized_needles = {needle.lower().replace("_", "-") for needle in needles}
    return any(
        needle in name or name in needle
        for name in normalized_names
        for needle in normalized_needles
    )


def _pytest_randomly_seed(config: pytest.Config) -> int | str | None:
    try:
        return config.getoption("randomly_seed")
    except (AttributeError, ValueError):
        return None


def _coverage_detected(plugin_names: list[str]) -> bool:
    if _plugin_detected(plugin_names, "cov", "pytest-cov", "coverage"):
        return True
    if "COVERAGE_PROCESS_START" in os.environ or "COV_CORE_SOURCE" in os.environ:
        return True
    if "coverage" in sys.modules:
        return True
    try:
        import coverage  # type: ignore[import-not-found]
    except Exception:
        return False
    current = getattr(getattr(coverage, "Coverage", None), "current", None)
    if current is None:
        return False
    try:
        return current() is not None
    except Exception:
        return False


def _ci_detected() -> bool:
    ci_names = (
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "BUILDKITE",
        "CIRCLECI",
        "JENKINS_URL",
        "TEAMCITY_VERSION",
    )
    return any(name in os.environ for name in ci_names)


def _render_failure_txt(manifest: dict[str, Any]) -> str:
    pytest_info = manifest["pytest"]
    failure = manifest["failure"]
    recording = manifest.get("recording") if isinstance(manifest.get("recording"), dict) else {}
    return "\n".join([
        f"test: {pytest_info['node_id']}",
        f"exception: {failure['exception_type']}",
        f"message: {failure['exception_message']}",
        "",
        "traceback_summary:",
        failure["traceback_summary"],
        "",
        f"recording: {manifest['recording_path']}",
        f"recording_available: {recording.get('available', True)}",
        f"recording_capture_method: {recording.get('capture_method', '')}",
        f"recording_failure_reason: {recording.get('failure_reason') or ''}",
        f"manifest: {manifest['manifest_path']}",
        "",
    ])


def _emit_next_steps(config: pytest.Config, manifest: dict[str, Any]) -> None:
    terminal = config.pluginmanager.get_plugin("terminalreporter")
    recording = manifest.get("recording") if isinstance(manifest.get("recording"), dict) else {}
    recording_available = recording.get("available", True)
    title = (
        "Retrace captured failed test:"
        if recording_available
        else "Retrace captured failed-test metadata, but recording failed:"
    )
    lines = [
        "",
        title,
        f"  test: {manifest['pytest']['node_id']}",
        f"  recording: {manifest['recording_path']}",
        f"  manifest: {manifest['manifest_path']}",
        f"  capture_method: {recording.get('capture_method', '')}",
    ]
    if recording_available:
        lines.extend([
            "",
            "Inspect:",
            "  retrace inspect --latest",
            "",
            "Use with agent:",
            "  retrace mcp --latest",
            "",
            "Open in VS Code:",
            "  retrace vscode --latest",
        ])
    else:
        lines.extend([
            f"  reason: {recording.get('failure_reason') or 'unknown'}",
            "",
            "Use with agent:",
            "  retrace agent-context --latest",
        ])
    lines.extend([
        "",
        "Artifacts are local and may contain runtime data. Delete with: retrace clean --all",
        "",
        f"RETRACE_RUN_ID={manifest['run_id']}",
        f"RETRACE_RECORDING={manifest['recording_path']}",
        f"RETRACE_MANIFEST={manifest['manifest_path']}",
    ])
    for line in lines:
        if terminal is not None:
            terminal.write_line(line)
        else:
            print(line)
