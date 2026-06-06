from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from retracesoftware import cli
from retracesoftware import pytest_plugin as plugin

pytest_plugins = ["pytester"]
REPO_ROOT = Path(__file__).resolve().parents[1]
_ORIGINAL_CAPTURE_FAILED_TEST_RECORDING = plugin._capture_failed_test_recording


@pytest.fixture(autouse=True)
def _restore_capture_helper(monkeypatch):
    monkeypatch.setattr(plugin, "_capture_failed_test_recording", _ORIGINAL_CAPTURE_FAILED_TEST_RECORDING)


def _run_pytest_with_plugin(pytester, *args):
    return pytester.runpytest("-p", "retracesoftware.pytest_plugin", *args)


def _install_successful_fake_capture(pytester):
    pytester.makeconftest("""
        import retracesoftware.pytest_plugin as plugin


        def pytest_configure(config):
            def fake_capture(recording_path, item):
                recording_path.parent.mkdir(parents=True, exist_ok=True)
                recording_path.write_bytes(b"fake real retrace recording")
                return plugin.RecordingCaptureResult(
                    available=True,
                    placeholder=False,
                    capture_method=plugin.EXISTING_CLI_CAPTURE_METHOD,
                )

            plugin._capture_failed_test_recording = fake_capture
    """)


def _install_failed_fake_capture(pytester):
    pytester.makeconftest("""
        import retracesoftware.pytest_plugin as plugin


        def pytest_configure(config):
            def fake_capture(recording_path, item):
                return plugin.RecordingCaptureResult(
                    available=False,
                    placeholder=False,
                    capture_method=plugin.EXISTING_CLI_CAPTURE_METHOD,
                    failure_reason="fake recorder unavailable",
                )

            plugin._capture_failed_test_recording = fake_capture
    """)


def _repo_test_pythonpath() -> str:
    entries = [str(REPO_ROOT / "src")]
    site_packages = sorted((REPO_ROOT / ".venv" / "lib").glob("python*/site-packages"))
    if site_packages:
        entries.append(str(site_packages[0]))
    if os.environ.get("PYTHONPATH"):
        entries.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(entries)


def _single_run_dir(base):
    run_dirs = list(base.glob("*"))
    assert len(run_dirs) == 1
    return run_dirs[0]


def test_capture_failed_test_recording_uses_existing_cli_subprocess(monkeypatch, tmp_path):
    calls = []
    recording_path = tmp_path / "New project" / "run one" / "recording.bin"

    class FakeItem:
        nodeid = "tests/test_example.py::test_failure"

    def fake_run(command, *, cwd, env, capture_output, text):
        calls.append({
            "command": command,
            "cwd": cwd,
            "env": env,
            "capture_output": capture_output,
            "text": text,
        })
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"real recording")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)

    capture = plugin._capture_failed_test_recording(recording_path, FakeItem())

    assert capture == plugin.RecordingCaptureResult(
        available=True,
        placeholder=False,
        capture_method="existing-cli-subprocess",
    )
    assert calls[0]["command"] == [
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
        "tests/test_example.py::test_failure",
    ]
    assert calls[0]["cwd"] == Path.cwd()
    assert calls[0]["capture_output"] is True
    assert calls[0]["text"] is True
    assert calls[0]["env"]["RETRACE_PYTEST_RECORDING_CHILD"] == "1"


def test_retrace_failing_test_creates_failed_run_artifacts(pytester):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    run_dir = _single_run_dir(pytester.path / ".retrace" / "runs")
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "failure.txt").is_file()
    assert (run_dir / "recording.bin").is_file()
    assert (run_dir / "recording.bin").read_bytes() == b"fake real retrace recording"

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pytest"]["node_id"].endswith("test_failure")
    assert manifest["failure"]["exception_type"] == "AssertionError"
    assert manifest["recording_path"].endswith("recording.bin")
    assert manifest["recording"] == {
        "available": True,
        "capture_method": "existing-cli-subprocess",
        "failure_reason": None,
        "placeholder": False,
    }


def test_retrace_failure_output_includes_next_step_commands(pytester):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            raise ValueError("boom")
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([
        "*Retrace captured failed test:*",
        "*capture_method: existing-cli-subprocess*",
        "*retrace inspect --latest*",
        "*retrace mcp --latest*",
        "*Artifacts are local and may contain runtime data. Delete with: retrace clean --all*",
        "*RETRACE_RECORDING=*",
        "*RETRACE_MANIFEST=*",
    ])


def test_retrace_recording_failure_keeps_manifest_and_failure_artifacts(pytester):
    _install_failed_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            raise RuntimeError("boom")
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    run_dir = _single_run_dir(pytester.path / ".retrace" / "runs")
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "failure.txt").is_file()
    assert not (run_dir / "recording.bin").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recording"]["available"] is False
    assert manifest["recording"]["placeholder"] is False
    assert manifest["recording"]["capture_method"] == "existing-cli-subprocess"
    assert manifest["recording"]["failure_reason"] == "fake recorder unavailable"
    result.stdout.fnmatch_lines([
        "*Retrace captured failed-test metadata, but recording failed:*",
        "*reason: fake recorder unavailable*",
        "*retrace agent-context --latest*",
    ])


def test_retrace_recording_child_guard_does_not_create_artifacts(pytester, monkeypatch):
    monkeypatch.setenv("RETRACE_PYTEST_RECORDING_CHILD", "1")
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    assert not (pytester.path / ".retrace" / "runs").exists()


def test_agent_context_latest_after_failed_retrace_run(pytester, monkeypatch, capsys):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            raise ValueError("boom")
    """)
    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")
    result.assert_outcomes(failed=1)
    monkeypatch.chdir(pytester.path)

    exit_code = cli.main(["agent-context", "--latest"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Retrace failed-test context" in output
    assert "test_failure" in output
    assert "ValueError: boom" in output
    assert "recording:" in output
    assert "manifest:" in output
    assert "failure:" in output
    assert "recording_available: yes" in output
    assert "recording_placeholder: no" in output
    assert "recording_capture_method: existing-cli-subprocess" in output
    assert "retrace inspect --latest" in output
    assert "retrace runs" in output
    assert "retrace mcp --latest" in output
    assert "retrace vscode --latest" in output
    assert "root cause" not in output.lower()
    assert "suggest" not in output.lower()


def test_agent_context_latest_json_after_failed_retrace_run(pytester, monkeypatch, capsys):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)
    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")
    result.assert_outcomes(failed=1)
    monkeypatch.chdir(pytester.path)
    capsys.readouterr()

    exit_code = cli.main(["agent-context", "--latest", "--json"])
    output = capsys.readouterr().out
    context = json.loads(output)

    assert exit_code == 0
    assert context["test"]["node_id"].endswith("test_failure")
    assert context["evidence"]["recording_exists"] is True
    assert context["evidence"]["recording_available"] is True
    assert context["evidence"]["recording_placeholder"] is False


def test_retrace_passing_test_does_not_create_run_by_default(pytester):
    pytester.makepyfile("""
        def test_passes():
            assert True
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(passed=1)
    assert not (pytester.path / ".retrace" / "runs").exists()


def test_retrace_manifest_does_not_include_env_values(pytester, monkeypatch, capsys):
    _install_successful_fake_capture(pytester)
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    run_dir = _single_run_dir(pytester.path / ".retrace" / "runs")
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
    pytester.makeconftest("""
        import retracesoftware.pytest_plugin as plugin


        def pytest_configure(config):
            def fake_capture(recording_path, item):
                recording_path.parent.mkdir(parents=True, exist_ok=True)
                recording_path.write_bytes(b"fake real retrace recording")
                return plugin.RecordingCaptureResult(
                    available=True,
                    placeholder=False,
                    capture_method=plugin.EXISTING_CLI_CAPTURE_METHOD,
                )

            plugin._capture_failed_test_recording = fake_capture
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

    result = _run_pytest_with_plugin(pytester, "--retrace", "-q")

    result.assert_outcomes(failed=1)
    run_dir = _single_run_dir(pytester.path / ".retrace" / "runs")
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
    _install_successful_fake_capture(pytester)
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
        "--retrace",
        "-q",
    )

    result.assert_outcomes(failed=1)
    assert (pytester.path / ".coverage").is_file()
    run_dir = _single_run_dir(pytester.path / ".retrace" / "runs")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["environment"]["coverage_detected"] is True
    assert manifest["pytest"]["coverage_detected"] is True


def test_retrace_output_dir_changes_run_directory(pytester):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(
        pytester,
        "--retrace",
        "--retrace-output-dir",
        "custom-runs",
        "-q",
    )

    result.assert_outcomes(failed=1)
    assert _single_run_dir(pytester.path / "custom-runs").is_dir()
    assert not (pytester.path / ".retrace" / "runs").exists()


def test_retrace_failed_only_mode_is_supported(pytester):
    _install_successful_fake_capture(pytester)
    pytester.makepyfile("""
        def test_failure():
            assert False
    """)

    result = _run_pytest_with_plugin(pytester, "--retrace", "--retrace-mode=failed-only", "-q")

    result.assert_outcomes(failed=1)
    assert _single_run_dir(pytester.path / ".retrace" / "runs").is_dir()


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
