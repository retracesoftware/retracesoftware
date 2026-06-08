from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from retracesoftware import cli
from retracesoftware import pytest_plugin as plugin

pytest_plugins = ["pytester"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_pytest_with_plugin(pytester, *args):
    return pytester.runpytest("-p", "retracesoftware.pytest_plugin", *args)


def _repo_test_pythonpath() -> str:
    entries = [str(REPO_ROOT / "src")]
    site_packages = sorted((REPO_ROOT / ".venv" / "lib").glob("python*/site-packages"))
    if site_packages:
        entries.append(str(site_packages[0]))
    if os.environ.get("PYTHONPATH"):
        entries.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(entries)


def _single_run_dir(base: Path) -> Path:
    run_dirs = list(base.glob("*"))
    assert len(run_dirs) == 1
    return run_dirs[0]


class _FakeConfig:
    class Option:
        numprocesses = None

    class PluginManager:
        def list_name_plugin(self):
            return []

    option = Option()
    pluginmanager = PluginManager()

    def __init__(
        self,
        *,
        args: tuple[str, ...] = ("--retrace",),
        output_dir: str = ".retrace/runs",
        mode: str = "failed-only",
    ) -> None:
        self.invocation_params = type("InvocationParams", (), {"args": args})()
        self._options = {
            "retrace": "--retrace" in args,
            "retrace_output_dir": output_dir,
            "retrace_mode": mode,
        }

    def getoption(self, name: str, default=None):
        return self._options.get(name, default)


def _install_fake_session_child(
    monkeypatch,
    *,
    returncode: int,
    write_manifest: bool = True,
    write_recording: bool = True,
) -> list[dict]:
    calls: list[dict] = []

    def fake_run_child(command, *, env):
        print("fake child stdout")
        print("fake child stderr", file=sys.stderr)
        calls.append({
            "command": command,
            "env": {
                plugin.RETRACE_PYTEST_REEXEC_PARENT: env.get(plugin.RETRACE_PYTEST_REEXEC_PARENT),
                plugin.RETRACE_PYTEST_RECORDED_CHILD: env.get(plugin.RETRACE_PYTEST_RECORDED_CHILD),
                plugin.RETRACE_PYTEST_RUN_ID: env.get(plugin.RETRACE_PYTEST_RUN_ID),
                plugin.RETRACE_PYTEST_RUN_DIR: env.get(plugin.RETRACE_PYTEST_RUN_DIR),
                plugin.RETRACE_PYTEST_RECORDING: env.get(plugin.RETRACE_PYTEST_RECORDING),
            },
        })
        run_dir = Path(env[plugin.RETRACE_PYTEST_RUN_DIR])
        recording = Path(env[plugin.RETRACE_PYTEST_RECORDING])
        manifest_path = run_dir / "manifest.json"
        failure_path = run_dir / "failure.txt"
        run_dir.mkdir(parents=True, exist_ok=True)
        if write_recording:
            recording.write_bytes(b"fake full-session recording")
        if write_manifest:
            from retracesoftware.pytest_runs import (
                build_failed_test_manifest,
                write_manifest as write_run_manifest,
            )

            manifest = build_failed_test_manifest(
                run_id=env[plugin.RETRACE_PYTEST_RUN_ID],
                recording_path=recording,
                manifest_path=manifest_path,
                failure_path=failure_path,
                node_id="tests/test_example.py::test_failure",
                test_file="tests/test_example.py",
                test_function="test_failure",
                exception_type="AssertionError",
                exception_message="simulated child failure",
                recording_placeholder=False,
                recording_capture_method=plugin.FULL_SESSION_CAPTURE_METHOD,
                recording_capture_scope=plugin.FULL_SESSION_CAPTURE_SCOPE,
                recording_failure_selection=plugin.FIRST_FAILURE_SELECTION,
                recording_available=True,
            )
            written = write_run_manifest(manifest, runs_dir=run_dir.parent)
            written_manifest = {**manifest, "manifest_path": str(written)}
            failure_path.write_text(plugin._render_failure_txt(written_manifest), encoding="utf-8")
        return returncode

    monkeypatch.setattr(plugin, "_run_child_process", fake_run_child)
    return calls


def _prepare_recorded_child(monkeypatch, pytester, *, run_id: str = "child-run") -> Path:
    run_dir = pytester.path / ".retrace" / "runs" / run_id
    recording_path = run_dir / "recording.bin"
    run_dir.mkdir(parents=True, exist_ok=True)
    recording_path.write_bytes(b"active full-session recording")
    monkeypatch.setenv(plugin.RETRACE_PYTEST_RECORDED_CHILD, "1")
    monkeypatch.setenv(plugin.RETRACE_PYTEST_RUN_ID, run_id)
    monkeypatch.setenv(plugin.RETRACE_PYTEST_RUN_DIR, str(run_dir))
    monkeypatch.setenv(plugin.RETRACE_PYTEST_RECORDING, str(recording_path))
    return run_dir


def test_parent_retrace_launches_full_session_recorded_child(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _install_fake_session_child(monkeypatch, returncode=7)
    config = _FakeConfig(args=("--retrace", "-q", "--maxfail=2"))

    exit_code = plugin._run_recorded_pytest_session(config)

    assert exit_code == 7
    call = calls[0]
    command = call["command"]
    assert command[:5] == [
        sys.executable,
        "-m",
        "retracesoftware",
        "--recording",
        call["env"][plugin.RETRACE_PYTEST_RECORDING],
    ]
    assert command[5:] == [
        "--format",
        "binary",
        "--stacktraces",
        "--",
        "-m",
        "pytest",
        "-q",
        "--maxfail=2",
    ]
    assert "--retrace" not in command
    assert "--maxfail=1" not in command
    assert call["env"][plugin.RETRACE_PYTEST_REEXEC_PARENT] == "1"
    assert call["env"][plugin.RETRACE_PYTEST_RECORDED_CHILD] == "1"
    assert call["env"][plugin.RETRACE_PYTEST_RUN_ID]


def test_parent_retrace_preserves_child_exit_code_and_output(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=3)

    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "fake child stdout" in captured.out
    assert "fake child stderr" in captured.err


def test_passing_recorded_child_deletes_temporary_artifact(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _install_fake_session_child(monkeypatch, returncode=0, write_manifest=False)

    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())

    assert exit_code == 0
    call = calls[0]
    assert not Path(call["env"][plugin.RETRACE_PYTEST_RUN_DIR]).exists()
    assert not (tmp_path / ".retrace" / "runs").exists()


def test_failing_recorded_child_keeps_run_artifacts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=1)

    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())

    assert exit_code == 1
    run_dir = _single_run_dir(tmp_path / ".retrace" / "runs")
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "failure.txt").is_file()
    assert (run_dir / "recording.bin").read_bytes() == b"fake full-session recording"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recording"] == {
        "available": True,
        "capture_method": "full-session-clean-subprocess",
        "capture_scope": "full_session",
        "failure_reason": None,
        "failure_selection": "first_failure",
        "placeholder": False,
    }


def test_failing_child_without_manifest_keeps_recording_with_fallback_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=2, write_manifest=False)

    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())

    assert exit_code == 2
    run_dir = _single_run_dir(tmp_path / ".retrace" / "runs")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pytest"]["node_id"] == "<session>"
    assert manifest["failure"]["exception_type"] == "PytestSessionFailure"
    assert manifest["recording"]["capture_method"] == "full-session-clean-subprocess"
    assert manifest["recording"]["capture_scope"] == "full_session"
    assert manifest["recording"]["failure_selection"] == "first_failure"


def test_child_args_remove_retrace_options_but_preserve_user_args(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _install_fake_session_child(monkeypatch, returncode=1)
    config = _FakeConfig(
        args=(
            "--retrace",
            "--retrace-mode=failed-only",
            "--retrace-output-dir",
            "custom-runs",
            "-k",
            "failure",
            "--maxfail=1",
            "-q",
        ),
        output_dir="custom-runs",
    )

    exit_code = plugin._run_recorded_pytest_session(config)

    assert exit_code == 1
    command = calls[0]["command"]
    assert "--retrace" not in command
    assert "--retrace-mode=failed-only" not in command
    assert "--retrace-output-dir" not in command
    assert "custom-runs" not in command
    assert command[-4:] == ["-k", "failure", "--maxfail=1", "-q"]
    assert _single_run_dir(tmp_path / "custom-runs").is_dir()


def test_recorded_child_call_failure_writes_first_failure_manifest(pytester, monkeypatch):
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makepyfile("""
        def test_first_failure():
            raise ValueError("first")

        def test_second_failure():
            raise RuntimeError("second")
    """)

    result = _run_pytest_with_plugin(pytester, "-q")

    result.assert_outcomes(failed=2)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pytest"]["node_id"].endswith("test_first_failure")
    assert manifest["failure"]["exception_type"] == "ValueError"
    assert manifest["failure"]["exception_message"] == "first"


def test_recorded_child_setup_failure_is_captured(pytester, monkeypatch):
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makepyfile("""
        import pytest

        @pytest.fixture
        def broken_fixture():
            raise RuntimeError("setup boom")

        def test_failure(broken_fixture):
            assert True
    """)

    result = _run_pytest_with_plugin(pytester, "-q")

    result.assert_outcomes(errors=1)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["failure"]["exception_type"] == "RuntimeError"
    assert manifest["failure"]["exception_message"] == "setup boom"


def test_recorded_child_teardown_failure_is_captured(pytester, monkeypatch):
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makepyfile("""
        import pytest

        @pytest.fixture
        def broken_teardown():
            yield
            raise RuntimeError("teardown boom")

        def test_failure(broken_teardown):
            assert True
    """)

    result = _run_pytest_with_plugin(pytester, "-q")

    result.assert_outcomes(passed=1, errors=1)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["failure"]["exception_type"] == "RuntimeError"
    assert manifest["failure"]["exception_message"] == "teardown boom"


def test_recorded_child_with_retrace_flag_does_not_reexec_again(pytester, monkeypatch):
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    assert (run_dir / "manifest.json").is_file()
    assert len(list((pytester.path / ".retrace" / "runs").glob("*"))) == 1


def test_agent_context_latest_after_failed_retrace_run(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=1)
    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())
    assert exit_code == 1

    exit_code = cli.main(["agent-context", "--latest"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Retrace failed-test context" in output
    assert "test_failure" in output
    assert "AssertionError: simulated child failure" in output
    assert "recording_available: yes" in output
    assert "recording_placeholder: no" in output
    assert "recording_capture_method: full-session-clean-subprocess" in output
    assert "capture_scope: full_session" in output
    assert "failure_selection: first_failure" in output
    assert "retrace inspect --latest" in output
    assert "retrace mcp --latest" in output
    assert "root cause" not in output.lower()
    assert "suggest" not in output.lower()


def test_agent_context_latest_json_after_failed_retrace_run(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=1)
    exit_code = plugin._run_recorded_pytest_session(_FakeConfig())
    assert exit_code == 1
    capsys.readouterr()

    exit_code = cli.main(["agent-context", "--latest", "--json"])
    output = capsys.readouterr().out
    context = json.loads(output)

    assert exit_code == 0
    assert context["test"]["node_id"].endswith("test_failure")
    assert context["evidence"]["recording_available"] is True
    assert context["evidence"]["recording_capture_method"] == "full-session-clean-subprocess"
    assert context["evidence"]["recording_capture_scope"] == "full_session"
    assert context["evidence"]["recording_failure_selection"] == "first_failure"


def test_retrace_manifest_does_not_include_env_values(pytester, monkeypatch, capsys):
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "-q")

    result.assert_outcomes(failed=1)
    raw_manifest = (run_dir / "manifest.json").read_text(encoding="utf-8")
    raw_failure = (run_dir / "failure.txt").read_text(encoding="utf-8")
    manifest = json.loads(raw_manifest)
    assert "DB_PASSWORD" in manifest["safety"]["env_var_names"]
    assert "supersecret" not in raw_manifest
    assert "supersecret" not in raw_failure
    assert manifest["safety"]["env_values_included"] is False

    monkeypatch.chdir(pytester.path)
    exit_code = cli.main(["agent-context", "--latest"])

    assert exit_code == 0
    assert "supersecret" not in capsys.readouterr().out


def test_retrace_manifest_records_ci_teamcity_and_plugin_metadata(pytester, monkeypatch):
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("TEAMCITY_VERSION", "2026.1-private-value")
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    pytester.makeconftest("""
        import retracesoftware.pytest_plugin as plugin


        def pytest_configure(config):
            plugin._active_plugin_names = lambda config: [
                "pytest-cov",
                "pytest-env",
                "pytest-randomly",
                "pytest-sugar",
                "teamcity-messages",
            ]
            plugin._pytest_randomly_seed = lambda config: 12345
    """)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "-q")

    result.assert_outcomes(failed=1)
    raw_manifest = (run_dir / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(raw_manifest)
    pytest_info = manifest["pytest"]
    environment = manifest["environment"]

    assert pytest_info["pytest_version"]
    assert pytest_info["active_plugins"] == [
        "pytest-cov",
        "pytest-env",
        "pytest-randomly",
        "pytest-sugar",
        "teamcity-messages",
    ]
    assert pytest_info["randomly_detected"] is True
    assert pytest_info["randomly_seed"] == 12345
    assert pytest_info["env_detected"] is True
    assert pytest_info["sugar_detected"] is True
    assert pytest_info["teamcity_messages_detected"] is True
    assert pytest_info["coverage_detected"] is True
    assert environment["coverage_detected"] is True
    assert environment["ci_detected"] is True
    assert environment["teamcity_detected"] is True
    assert "TEAMCITY_VERSION" in manifest["safety"]["env_var_names"]
    assert "2026.1-private-value" not in raw_manifest


def test_coverage_detected_from_runtime_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "coverage", object())

    assert plugin._coverage_detected([]) is True


def test_coverage_invocation_creates_failed_run_artifacts(pytester, monkeypatch):
    if importlib.util.find_spec("coverage") is None:
        pytest.skip("coverage.py is not installed")
    run_dir = _prepare_recorded_child(monkeypatch, pytester)
    monkeypatch.setenv("PYTHONPATH", _repo_test_pythonpath())
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = pytester.run(
        sys.executable,
        "-S",
        "-m",
        "coverage",
        "run",
        "-m",
        "pytest",
        "-p",
        "retracesoftware.pytest_plugin",
        "-q",
    )

    result.assert_outcomes(failed=1)
    assert (pytester.path / ".coverage").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["environment"]["coverage_detected"] is True
    assert manifest["pytest"]["coverage_detected"] is True


def test_retrace_failed_only_mode_is_supported(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_session_child(monkeypatch, returncode=1)

    exit_code = plugin._run_recorded_pytest_session(
        _FakeConfig(args=("--retrace", "--retrace-mode=failed-only")),
    )

    assert exit_code == 1
    assert _single_run_dir(tmp_path / ".retrace" / "runs").is_dir()


def test_retrace_unsupported_mode_fails_clearly(pytester):
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "--retrace-mode=all", "-q")

    assert result.ret != 0
    result.stderr.fnmatch_lines([
        "*unsupported --retrace-mode='all'; only 'failed-only' is implemented*",
    ])


def test_xdist_is_out_of_scope_for_retrace_v1():
    class FakeConfig:
        class Option:
            numprocesses = 2

        option = Option()

        class InvocationParams:
            args = ("-n", "2")

        invocation_params = InvocationParams()

    assert plugin._xdist_requested(FakeConfig()) is True
