"""Opt-in pytest integration for failed-test Retrace run artifacts."""

from __future__ import annotations

import os
import shutil
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

RETRACE_PYTEST_REEXEC_PARENT = "RETRACE_PYTEST_REEXEC_PARENT"
RETRACE_PYTEST_RECORDED_CHILD = "RETRACE_PYTEST_RECORDED_CHILD"
RETRACE_PYTEST_RUN_ID = "RETRACE_PYTEST_RUN_ID"
RETRACE_PYTEST_RUN_DIR = "RETRACE_PYTEST_RUN_DIR"
RETRACE_PYTEST_RECORDING = "RETRACE_PYTEST_RECORDING"
FULL_SESSION_CAPTURE_METHOD = "full-session-clean-subprocess"
FULL_SESSION_CAPTURE_SCOPE = "full_session"
FIRST_FAILURE_SELECTION = "first_failure"


@dataclass(frozen=True)
class RecordedRunPaths:
    run_id: str
    run_dir: Path
    recording_path: Path
    manifest_path: Path
    failure_path: Path


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
    if not _pytest_retrace_active(config):
        return
    mode = config.getoption("retrace_mode")
    if mode != "failed-only":
        raise pytest.UsageError(f"unsupported --retrace-mode={mode!r}; only 'failed-only' is implemented")
    if _is_recorded_child():
        config._retrace_first_failure_written = False  # type: ignore[attr-defined]


def pytest_cmdline_main(config: pytest.Config) -> int | None:
    if _is_recorded_child():
        return None
    if not config.getoption("retrace", default=False):
        return None
    mode = config.getoption("retrace_mode")
    if mode != "failed-only":
        raise pytest.UsageError(f"unsupported --retrace-mode={mode!r}; only 'failed-only' is implemented")
    if _xdist_requested(config):
        print(
            "pytest --retrace does not support pytest-xdist in v1. "
            "Run without xdist or record each worker separately in a future version.",
            file=sys.stderr,
        )
        return int(pytest.ExitCode.USAGE_ERROR)
    return _run_recorded_pytest_session(config)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    report = yield
    config = item.config
    if not _is_recorded_child():
        return report
    if not report.failed:
        return report

    if getattr(config, "_retrace_first_failure_written", False):
        return report
    config._retrace_first_failure_written = True  # type: ignore[attr-defined]

    try:
        manifest = _write_failed_test_artifacts(item, call, report)
    except Exception as exc:  # noqa: BLE001 - surface metadata failure without hiding pytest failure
        _emit_metadata_error(config, exc)
    else:
        _emit_next_steps(config, manifest)
    return report


def _is_recorded_child() -> bool:
    return os.environ.get(RETRACE_PYTEST_RECORDED_CHILD) == "1"


def _pytest_retrace_active(config: pytest.Config) -> bool:
    return _is_recorded_child() or config.getoption("retrace", default=False)


def _run_recorded_pytest_session(config: pytest.Config) -> int:
    run_id = create_run_id()
    output_dir = Path(config.getoption("retrace_output_dir"))
    run_dir = output_dir / run_id
    recording_path = run_dir / "recording.bin"
    manifest_path = run_dir / "manifest.json"
    failure_path = run_dir / "failure.txt"
    run_dir.mkdir(parents=True, exist_ok=True)

    child_args = _child_pytest_args(config)
    command = _recorded_child_command(recording_path, child_args)
    env = _recorded_child_env(
        run_id=run_id,
        run_dir=run_dir,
        recording_path=recording_path,
    )

    try:
        returncode = _run_child_process(command, env=env)
    except OSError as exc:
        _remove_temporary_run(run_dir)
        print(f"pytest --retrace failed: could not start recorded pytest child: {exc}", file=sys.stderr)
        return 1

    if returncode == 0:
        _remove_temporary_run(run_dir)
        return returncode

    if not manifest_path.is_file():
        _write_session_failure_fallback_manifest(
            run_id=run_id,
            run_dir=run_dir,
            recording_path=recording_path,
            manifest_path=manifest_path,
            failure_path=failure_path,
            returncode=returncode,
            config=config,
        )
    return returncode


def _child_pytest_args(config: pytest.Config) -> list[str]:
    raw_args = [str(arg) for arg in config.invocation_params.args]
    return _remove_retrace_args(raw_args)


def _remove_retrace_args(args: list[str]) -> list[str]:
    value_options = {
        "--retrace-output-dir",
        "--retrace-mode",
        "--retrace-max-runs",
    }
    filtered: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--retrace":
            index += 1
            continue
        if any(arg.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if arg in value_options:
            index += 2 if index + 1 < len(args) else 1
            continue
        filtered.append(arg)
        index += 1
    return filtered


def _recorded_child_command(recording_path: Path, pytest_args: list[str]) -> list[str]:
    return [
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
        *pytest_args,
    ]


def _recorded_child_env(*, run_id: str, run_dir: Path, recording_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[RETRACE_PYTEST_REEXEC_PARENT] = "1"
    env[RETRACE_PYTEST_RECORDED_CHILD] = "1"
    env[RETRACE_PYTEST_RUN_ID] = run_id
    env[RETRACE_PYTEST_RUN_DIR] = str(run_dir)
    env[RETRACE_PYTEST_RECORDING] = str(recording_path)
    return env


def _run_child_process(command: list[str], *, env: dict[str, str]) -> int:
    process = subprocess.Popen(command, cwd=Path.cwd(), env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        _terminate_child_process(process)
        return 130
    except BaseException:
        _terminate_child_process(process)
        raise


def _terminate_child_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _remove_temporary_run(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)
    _remove_empty_dir(run_dir.parent)
    if run_dir.parent.name == "runs":
        _remove_empty_dir(run_dir.parent.parent)


def _remove_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        return


def _recorded_child_run_paths(config: pytest.Config) -> RecordedRunPaths:
    run_id = os.environ.get(RETRACE_PYTEST_RUN_ID)
    run_dir = os.environ.get(RETRACE_PYTEST_RUN_DIR)
    recording_path = os.environ.get(RETRACE_PYTEST_RECORDING)
    if not run_id or not run_dir or not recording_path:
        output_dir = Path(config.getoption("retrace_output_dir"))
        fallback_run_id = run_id or create_run_id()
        fallback_run_dir = output_dir / fallback_run_id
        fallback_recording_path = fallback_run_dir / "recording.bin"
        return RecordedRunPaths(
            run_id=fallback_run_id,
            run_dir=fallback_run_dir,
            recording_path=fallback_recording_path,
            manifest_path=fallback_run_dir / "manifest.json",
            failure_path=fallback_run_dir / "failure.txt",
        )
    resolved_run_dir = Path(run_dir)
    return RecordedRunPaths(
        run_id=run_id,
        run_dir=resolved_run_dir,
        recording_path=Path(recording_path),
        manifest_path=resolved_run_dir / "manifest.json",
        failure_path=resolved_run_dir / "failure.txt",
    )


def _write_session_failure_fallback_manifest(
    *,
    run_id: str,
    run_dir: Path,
    recording_path: Path,
    manifest_path: Path,
    failure_path: Path,
    returncode: int,
    config: pytest.Config,
) -> None:
    recording_available = recording_path.exists() and recording_path.stat().st_size > 0
    reason = (
        "pytest exited before Retrace observed a test-report failure"
        if recording_available
        else "pytest exited before a replayable recording was available"
    )
    active_plugins = _active_plugin_names(config)
    manifest = build_failed_test_manifest(
        run_id=run_id,
        recording_path=recording_path,
        manifest_path=manifest_path,
        failure_path=failure_path,
        node_id="<session>",
        test_function="<session>",
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
        exception_type="PytestSessionFailure",
        exception_message=f"pytest exited with code {returncode}",
        traceback_summary=reason,
        recording_placeholder=False,
        recording_capture_method=FULL_SESSION_CAPTURE_METHOD,
        recording_capture_scope=FULL_SESSION_CAPTURE_SCOPE,
        recording_failure_selection=FIRST_FAILURE_SELECTION,
        recording_available=recording_available,
        recording_failure_reason=reason,
        cwd=Path.cwd(),
        coverage_detected=_coverage_detected(active_plugins),
        teamcity_detected="TEAMCITY_VERSION" in os.environ,
        ci_detected=_ci_detected(),
        env_var_names=sorted(os.environ),
    )
    written_manifest_path = write_manifest(manifest, runs_dir=run_dir.parent)
    written_manifest = {**manifest, "manifest_path": str(written_manifest_path)}
    failure_path.write_text(_render_failure_txt(written_manifest), encoding="utf-8")
    print(
        "Retrace kept the session recording, but no test-report failure metadata was observed.",
        file=sys.stderr,
    )
    print(f"  recording: {recording_path}", file=sys.stderr)
    print(f"  manifest: {written_manifest_path}", file=sys.stderr)


def _xdist_requested(config: pytest.Config) -> bool:
    numprocesses = getattr(config.option, "numprocesses", None)
    if numprocesses not in (None, 0, "0"):
        return True
    args = [str(arg) for arg in config.invocation_params.args]
    for index, arg in enumerate(args):
        if arg == "-n":
            value = args[index + 1] if index + 1 < len(args) else ""
            return value not in {"", "0"}
        if arg.startswith("-n") and arg != "-n":
            return arg[2:] != "0"
        if arg == "--numprocesses":
            value = args[index + 1] if index + 1 < len(args) else ""
            return value not in {"", "0"}
        if arg.startswith("--numprocesses="):
            return arg.split("=", 1)[1] != "0"
    return False


def _emit_metadata_error(config: pytest.Config, exc: Exception) -> None:
    message = f"Retrace could not write failed-test metadata: {exc}"
    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(message)
    else:
        print(message, file=sys.stderr)


def _write_failed_test_artifacts(
    item: pytest.Item,
    call: pytest.CallInfo[Any],
    report: pytest.TestReport,
) -> dict[str, Any]:
    config = item.config
    paths = _recorded_child_run_paths(config)
    paths.run_dir.mkdir(parents=True, exist_ok=True)

    exception_type, exception_message = _exception_details(call, report)
    traceback_summary = _traceback_summary(report)
    active_plugins = _active_plugin_names(config)
    env_var_names = sorted(os.environ)
    test_file, test_line, test_function = _item_location(item)

    manifest = build_failed_test_manifest(
        run_id=paths.run_id,
        recording_path=paths.recording_path,
        manifest_path=paths.manifest_path,
        failure_path=paths.failure_path,
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
        recording_placeholder=False,
        recording_capture_method=FULL_SESSION_CAPTURE_METHOD,
        recording_capture_scope=FULL_SESSION_CAPTURE_SCOPE,
        recording_failure_selection=FIRST_FAILURE_SELECTION,
        recording_available=True,
        recording_failure_reason=None,
        cwd=Path.cwd(),
        coverage_detected=_coverage_detected(active_plugins),
        teamcity_detected="TEAMCITY_VERSION" in os.environ,
        ci_detected=_ci_detected(),
        env_var_names=env_var_names,
    )

    written_manifest_path = write_manifest(manifest, runs_dir=paths.run_dir.parent)
    written_manifest = {
        **manifest,
        "manifest_path": str(written_manifest_path),
    }
    paths.failure_path.write_text(_render_failure_txt(written_manifest), encoding="utf-8")
    return {
        **written_manifest,
        "manifest_path": str(written_manifest_path),
    }


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
