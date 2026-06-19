from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest
from retracesoftware import retracepython
from retracesoftware.retrace_venv import (
    _wrapper_script,
    activation_pth_source,
    disable_current_hook,
    enable_current_hook,
)


def test_retracepython_builds_command_and_resolves_module_recording(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING_INODE", raising=False)

    prepared: list[str] = []
    monkeypatch.setattr(retracepython, "_prepare_trace_file", prepared.append)

    executable, argv, env = retracepython.build_retrace_command(
        ["-m", "pytest", "-q"],
        recording="{script}.retrace",
    )

    assert executable == os.environ.get("RETRACE_REAL_PYTHON", executable)
    assert argv[:3] == [executable, "-m", "retracesoftware"]
    assert argv[-4:] == ["--", "-m", "pytest", "-q"]
    assert "--recording" in argv
    assert argv[argv.index("--recording") + 1] == "pytest.retrace"
    assert prepared == ["pytest.retrace"]
    assert env["RETRACE_RECORDING"] == "pytest.retrace"
    assert env[retracepython.VENV_BOOTSTRAP_DISABLE_ENV] == "1"


def test_retracepython_disable_recording_does_not_prepare_trace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)

    prepared: list[str] = []
    monkeypatch.setattr(retracepython, "_prepare_trace_file", prepared.append)

    _, argv, env = retracepython.build_retrace_command(
        ["script.py"],
        recording="disable",
    )

    assert "--recording" in argv
    assert argv[argv.index("--recording") + 1] == "disable"
    assert env["RETRACE_RECORDING"] == "disable"
    assert env[retracepython.VENV_BOOTSTRAP_DISABLE_ENV] == "1"
    assert prepared == []


def test_retracepython_can_propagate_to_venv_child_pythons(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(retracepython.VENV_BOOTSTRAP_DISABLE_ENV, "1")
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.setenv("RETRACE_PYTHON_WRAPPER", "/tmp/retrace-python")

    monkeypatch.setattr(retracepython, "_prepare_trace_file", lambda path: None)

    _, _, env = retracepython.build_retrace_command(
        ["script.py"],
        recording="trace.retrace",
        propagate_to_child_pythons=True,
    )

    assert retracepython.VENV_BOOTSTRAP_DISABLE_ENV not in env
    assert env["PYTHONEXECUTABLE"] == "/tmp/retrace-python"


def test_retracepython_auto_debug_env_truth_table() -> None:
    assert retracepython.auto_debug_enabled({"RETRACE_AUTO_DEBUG": "1"})
    assert retracepython.auto_debug_enabled({"RETRACE_AUTO_DEBUG": "true"})
    assert not retracepython.auto_debug_enabled({"RETRACE_AUTO_DEBUG": "0"})
    assert not retracepython.auto_debug_enabled({"RETRACE_AUTO_DEBUG": "false"})
    assert not retracepython.auto_debug_enabled({})
    assert not retracepython.auto_debug_enabled(
        {
            "RETRACE_AUTO_DEBUG": "1",
            "RETRACE_AUTO_DEBUG_SUPERVISED": "1",
        }
    )


def test_retracepython_auto_debug_runs_ai_driver_on_nonzero(
    monkeypatch,
    tmp_path: Path,
) -> None:
    recording = tmp_path / "trace.retrace"
    recording.write_text("trace", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, env):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 7 if len(calls) == 1 else 0)

    monkeypatch.setattr(retracepython.subprocess, "run", fake_run)
    monkeypatch.setenv("RETRACE_AI_DRIVER_COMMAND", "/bin/echo ai-driver")
    monkeypatch.setenv("RETRACE_REPLAY_BIN", "/tmp/replay")
    monkeypatch.setenv("RETRACE_AUTO_DEBUG", "1")
    monkeypatch.setenv("RETRACE_INODE", "legacy")
    monkeypatch.delenv("RETRACE_AI_SERVER", raising=False)

    rc = retracepython.run_with_auto_debug(
        "/real/python",
        ["/real/python", "-m", "retracesoftware", "--", "app.py"],
        {"RETRACE_RECORDING": str(recording)},
        ["app.py"],
    )

    assert rc == 7
    assert calls[0][0] == ["/real/python", "-m", "retracesoftware", "--", "app.py"]
    assert calls[0][1]["RETRACE_AUTO_DEBUG_SUPERVISED"] == "1"
    assert calls[1][0][:2] == ["/bin/echo", "ai-driver"]
    assert calls[1][0][calls[1][0].index("--tool-executor") + 1] == "dap"
    assert calls[1][0][calls[1][0].index("--trace") + 1] == str(recording)
    assert calls[1][0][calls[1][0].index("--replay-bin") + 1] == "/tmp/replay"
    assert calls[1][0][calls[1][0].index("--report-out") + 1] == str(recording.with_suffix(".ai-report.json"))
    assert calls[1][0][calls[1][0].index("--report-md") + 1] == str(recording.with_suffix(".ai-report.md"))
    assert "Target command: app.py." in calls[1][0][calls[1][0].index("--task") + 1]
    assert calls[1][1]["RETRACEPYTHON_BYPASS"] == "1"
    assert calls[1][1]["RETRACE_AI_SERVER"] == retracepython.DEFAULT_AI_SERVER
    assert retracepython.VENV_BOOTSTRAP_DISABLE_ENV not in calls[1][1]
    assert "RETRACE_RECORDING" not in calls[1][1]
    assert "RETRACE_CONFIG" not in calls[1][1]
    assert "RETRACE_AUTO_DEBUG" not in calls[1][1]
    assert "RETRACE_INODE" not in calls[1][1]


def test_retracepython_ai_debugger_env_preserves_custom_server(monkeypatch) -> None:
    monkeypatch.setenv("RETRACE_AI_SERVER", "http://localhost:8787")

    env = retracepython.ai_debugger_env()

    assert env["RETRACE_AI_SERVER"] == "http://localhost:8787"


def test_retracepython_ai_driver_defaults_to_in_package_driver(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RETRACE_AI_DRIVER_COMMAND", raising=False)
    monkeypatch.delenv("RETRACE_AI_DRIVER", raising=False)

    command = retracepython.build_ai_driver_command(
        recording=tmp_path / "trace.retrace",
        report_out=tmp_path / "trace.ai-report.json",
        report_md=tmp_path / "trace.ai-report.md",
        target_exit=1,
        target_argv=["app.py"],
    )

    assert command[:3] == [sys.executable, "-m", "retracesoftware.ai_driver"]


def test_retracepython_auto_debug_live_service_autogenerates_reports(
    tmp_path: Path,
) -> None:
    if os.environ.get("RETRACE_RUN_AI_E2E") != "1":
        pytest.skip("set RETRACE_RUN_AI_E2E=1 to call the hosted Retrace AI service")

    env = os.environ.copy()
    env.update(
        {
            "PYTHONFAULTHANDLER": "1",
            "RETRACE_AUTO_DEBUG": "1",
            "RETRACE_INSTALL_ID_FILE": str(tmp_path / "install_id"),
        }
    )
    for key in (
        "RETRACE",
        "RETRACE_CONFIG",
        "RETRACE_RECORDING",
        "RETRACE_AI_REPORT_OUT",
        "RETRACE_AI_REPORT_MD",
        "RETRACE_AI_SERVER",
        "RETRACE_AUTO_DEBUG_SUPERVISED",
        retracepython.VENV_BOOTSTRAP_DISABLE_ENV,
    ):
        env.pop(key, None)
    from retracesoftware.replay import binary_path

    env["RETRACE_REPLAY_BIN"] = binary_path()

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware.retracepython",
            "-c",
            "raise RuntimeError('live auto debug smoke')",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    recording = tmp_path / "command.retrace"
    report_out = tmp_path / "command.ai-report.json"
    report_md = tmp_path / "command.ai-report.md"

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert recording.exists()
    assert report_out.exists(), f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert report_md.exists(), f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "running AI debugger" in proc.stderr
    report = json.loads(report_out.read_text(encoding="utf-8"))
    assert report["kind"] == "retrace_ai_driver_run"
    assert report["debug_session_id"]
    assert report["trace"] == str(recording.resolve())
    assert isinstance(report["report"], dict)
    assert report["status"] not in {"blocked", "error"}, json.dumps(report, indent=2)
    assert report["report"].get("failure_domain") != "retrace", json.dumps(report, indent=2)
    transcript = report.get("transcript") or []
    assert any(
        action.get("tool") == "get_stack_trace"
        and (action.get("result") or {}).get("data", {}).get("stack_frames")
        for action in transcript
    ), json.dumps(report, indent=2)
    assert any(
        (
            action.get("tool") == "get_exception_info"
            and "RuntimeError" in json.dumps((action.get("result") or {}).get("data", {}))
        )
        or (
            action.get("tool") == "get_stack_trace"
            and "RuntimeError" in json.dumps((action.get("result") or {}).get("data", {}))
        )
        for action in transcript
    ), json.dumps(report, indent=2)
    assert "AI report written" in proc.stderr
    assert report_md.read_text(encoding="utf-8").startswith("# ")


def test_retracepython_auto_debug_skips_ai_driver_for_success(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, *, env):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(retracepython.subprocess, "run", fake_run)

    rc = retracepython.run_with_auto_debug(
        "/real/python",
        ["/real/python", "-m", "retracesoftware", "--", "app.py"],
        {"RETRACE_RECORDING": "trace.retrace"},
    )

    assert rc == 0
    assert calls == [["/real/python", "-m", "retracesoftware", "--", "app.py"]]


def test_retracepython_auto_debug_deletes_default_recording_on_success(
    monkeypatch,
    tmp_path: Path,
) -> None:
    recording = tmp_path / "app.retrace"
    recording.write_text("trace", encoding="utf-8")

    def fake_run(command, *, env):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(retracepython.subprocess, "run", fake_run)

    rc = retracepython.run_with_auto_debug(
        "/real/python",
        ["/real/python", "-m", "retracesoftware", "--", "app.py"],
        {"RETRACE_RECORDING": str(recording)},
        cleanup_recording_on_success=True,
    )

    assert rc == 0
    assert not recording.exists()


def test_retracepython_auto_debug_keeps_explicit_recording_on_success(
    monkeypatch,
    tmp_path: Path,
) -> None:
    recording = tmp_path / "explicit.retrace"
    recording.write_text("trace", encoding="utf-8")

    def fake_run(command, *, env):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(retracepython.subprocess, "run", fake_run)

    rc = retracepython.run_with_auto_debug(
        "/real/python",
        ["/real/python", "-m", "retracesoftware", "--", "app.py"],
        {"RETRACE_RECORDING": str(recording)},
        cleanup_recording_on_success=False,
    )

    assert rc == 0
    assert recording.exists()


def test_current_hook_activation_pth_skips_retrace_import_without_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RETRACE", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)

    original_import = builtins.__import__

    def import_guard(name, *args, **kwargs):
        if name == "retracesoftware" or name.startswith("retracesoftware."):
            raise AssertionError(f"unexpected Retrace import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_guard)

    exec(compile(activation_pth_source(), "retracesoftware_hook.pth", "exec"), {})


def test_current_hook_activation_pth_imports_bootstrap_with_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RETRACE", "1")
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)

    imported: list[str] = []
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        imported.append(name)
        if name == "retracesoftware.retrace_venv_bootstrap":
            return object()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    exec(compile(activation_pth_source(), "retracesoftware_hook.pth", "exec"), {})

    assert "retracesoftware.retrace_venv_bootstrap" in imported


def test_current_hook_enable_and_disable_write_pth_files(tmp_path: Path) -> None:
    target = tmp_path / "retracesoftware_hook.pth"
    paths_target = tmp_path / "retracesoftware_00_paths.pth"

    assert enable_current_hook(target) == (target, paths_target)
    assert target.read_text(encoding="utf-8") == activation_pth_source()
    linked_paths = [
        Path(line)
        for line in paths_target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert linked_paths
    assert all(path.is_absolute() for path in linked_paths)
    assert all(path.exists() for path in linked_paths)
    assert disable_current_hook(target) == [target, paths_target]
    assert not target.exists()
    assert not paths_target.exists()
    assert disable_current_hook(target) == []


def test_current_hook_bootstrap_bypass_rules(monkeypatch) -> None:
    monkeypatch.delenv("RETRACE", raising=False)
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)

    from retracesoftware import retrace_venv_bootstrap

    assert retrace_venv_bootstrap.activation_requested({"RETRACE": "1"})
    assert retrace_venv_bootstrap.activation_requested({"RETRACE_AUTO_DEBUG": "1"})
    assert retrace_venv_bootstrap.activation_requested({"RETRACE_RECORDING": "trace.retrace"})
    assert not retrace_venv_bootstrap.activation_requested({"RETRACE": "0"})
    assert not retrace_venv_bootstrap.activation_requested({"RETRACE_AUTO_DEBUG": "0"})
    assert retrace_venv_bootstrap.should_bypass([])
    assert retrace_venv_bootstrap.should_bypass(["-m", "pip", "install", "x"])
    assert retrace_venv_bootstrap.should_bypass(["-m", "retracesoftware", "--help"])
    assert retrace_venv_bootstrap.should_bypass(["-c", "from multiprocessing.spawn import spawn_main; spawn_main()"])
    assert not retrace_venv_bootstrap.should_bypass(["app.py"])


def _write_fake_real_python(path: Path, log: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "{",
                '  printf "args:%s\\n" "$*"',
                '  printf "PYTHONEXECUTABLE:%s\\n" "${PYTHONEXECUTABLE-}"',
                '  printf "RETRACE_REAL_PYTHON:%s\\n" "${RETRACE_REAL_PYTHON-}"',
                '  printf "RETRACE_PYTHON_WRAPPER:%s\\n" "${RETRACE_PYTHON_WRAPPER-}"',
                f"}} >> {str(log)!r}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_wrapper(path: Path, real_python: Path) -> None:
    path.write_text(_wrapper_script(real_python), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_retrace_venv_wrapper_bypasses_pip_and_retrace_modules(
    tmp_path: Path,
) -> None:
    log = tmp_path / "log.txt"
    real_python = tmp_path / "real-python"
    wrapper = tmp_path / "python"
    _write_fake_real_python(real_python, log)
    _write_wrapper(wrapper, real_python)

    env = os.environ.copy()
    env["PYTHONEXECUTABLE"] = "inherited"
    subprocess.run(
        [str(wrapper), "-m", "pip", "--version"],
        check=True,
        env=env,
    )
    subprocess.run(
        [str(wrapper), "-m", "retracesoftware", "--help"],
        check=True,
        env=env,
    )

    entries = log.read_text(encoding="utf-8")
    assert "args:-m pip --version" in entries
    assert "args:-m retracesoftware --help" in entries
    assert "args:-m retracesoftware.retracepython" not in entries
    assert "PYTHONEXECUTABLE:inherited" not in entries


def test_retrace_venv_wrapper_traces_regular_python_commands(
    tmp_path: Path,
) -> None:
    log = tmp_path / "log.txt"
    real_python = tmp_path / "real-python"
    wrapper = tmp_path / "python"
    _write_fake_real_python(real_python, log)
    _write_wrapper(wrapper, real_python)

    subprocess.run([str(wrapper), "script.py", "arg"], check=True)
    subprocess.run([str(wrapper), "-c", "print('ok')"], check=True)

    entries = log.read_text(encoding="utf-8")
    assert "args:-m retracesoftware.retracepython script.py arg" in entries
    assert "args:-m retracesoftware.retracepython -c print('ok')" in entries
    assert f"PYTHONEXECUTABLE:{wrapper}" in entries
    assert f"RETRACE_REAL_PYTHON:{real_python}" in entries
    assert f"RETRACE_PYTHON_WRAPPER:{wrapper}" in entries
