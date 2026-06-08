from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _write_mini_project(project: Path) -> None:
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_example.py").write_text(
        textwrap.dedent(
            """
            def test_passes():
                assert 1 + 1 == 2


            def test_fails():
                payload = {"expected": 2, "actual": 3}
                assert payload["actual"] == payload["expected"]
            """,
        ),
        encoding="utf-8",
    )


def _venv_bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if sys.platform == "win32" else "bin")


def _repo_site_packages() -> Path:
    site_packages = sorted((REPO_ROOT / ".venv" / "lib").glob("python*/site-packages"))
    if not site_packages:
        pytest.skip("repo .venv site-packages are required for source-tree E2E")
    return site_packages[0]


def _native_extension_paths() -> list[Path]:
    build_root = REPO_ROOT / "build" / f"cp{sys.version_info.major}{sys.version_info.minor}" / "cpp"
    extension_dirs = [
        build_root / "functional",
        build_root / "utils",
        build_root / "stream",
        build_root / "cursor",
    ]
    missing_dirs = [path for path in extension_dirs if not path.is_dir()]
    if missing_dirs:
        pytest.skip(f"native extension build dirs are required for source-tree E2E: {missing_dirs}")
    return extension_dirs


def _dev_import_entries(site_packages: Path) -> list[str]:
    return [str(REPO_SRC), *[str(path) for path in _native_extension_paths()], str(site_packages)]


def _dev_env(site_packages: Path) -> dict[str, str]:
    env = dict(os.environ)
    entries = _dev_import_entries(site_packages)
    if env.get("PYTHONPATH"):
        entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(entries)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTEST_ADDOPTS"] = "-p retracesoftware.pytest_plugin --retrace"
    return env


def _source_tree_python_code(body: str, *, site_packages: Path) -> str:
    return "\n".join(
        [
            "import sys",
            f"sys.path[:0] = {_dev_import_entries(site_packages)!r}",
            body,
        ],
    )


def _run_source_tree_pytest(
    python: str,
    *,
    cwd: Path,
    env: dict[str, str],
    site_packages: Path,
) -> subprocess.CompletedProcess[str]:
    code = _source_tree_python_code(
        "import pytest\n"
        "raise SystemExit(pytest.main(['-p', 'retracesoftware.pytest_plugin', '--retrace', 'tests/test_example.py']))",
        site_packages=site_packages,
    )
    return _run([python, "-S", "-c", code], cwd=cwd, env=env, timeout=180)


def _run_source_tree_cli(
    python: str,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    site_packages: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    code = _source_tree_python_code(
        "import json\n"
        "from retracesoftware.cli import main\n"
        "raise SystemExit(main(json.loads(sys.argv[1])))",
        site_packages=site_packages,
    )
    return _run([python, "-S", "-c", code, json.dumps(args)], cwd=cwd, env=env, timeout=timeout)


def _assert_failed_run_artifacts(project: Path, pytest_result: subprocess.CompletedProcess[str]) -> Path:
    runs_dir = project / ".retrace" / "runs"
    assert runs_dir.is_dir(), (
        f"pytest stdout:\n{pytest_result.stdout}\n"
        f"pytest stderr:\n{pytest_result.stderr}"
    )
    run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    manifest_path = run_dir / "manifest.json"
    failure_path = run_dir / "failure.txt"
    recording_path = run_dir / "recording.bin"
    assert manifest_path.is_file()
    assert failure_path.is_file()
    assert recording_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["recording"]["available"] is True
    assert manifest["recording"]["placeholder"] is False
    assert manifest["recording"]["capture_method"] == "full-session-clean-subprocess"
    assert manifest["recording"]["capture_scope"] == "full_session"
    assert manifest["recording"]["failure_selection"] == "first_failure"
    manifest_recording_path = Path(manifest["recording_path"])
    if not manifest_recording_path.is_absolute():
        manifest_recording_path = project / manifest_recording_path
    assert manifest_recording_path.exists()
    assert "New project" in str(manifest_recording_path)
    assert len([path for path in runs_dir.iterdir() if path.is_dir()]) == 1
    return run_dir


@pytest.mark.skipif(
    os.environ.get("RETRACE_RUN_PYTEST_AGENT_DEV_E2E") != "1",
    reason="opt-in source-tree end-to-end smoke; set RETRACE_RUN_PYTEST_AGENT_DEV_E2E=1 to run",
)
def test_pytest_agent_workflow_source_tree_dev_mode_with_path_spaces(tmp_path):
    project = tmp_path / "New project"
    _write_mini_project(project)

    venv_dir = project / ".venv-dev"
    venv = _run([sys.executable, "-S", "-m", "venv", str(venv_dir)], cwd=project)
    assert venv.returncode == 0, venv.stderr

    python = str(_venv_bin_dir(venv_dir) / "python")
    site_packages = _repo_site_packages()
    env = _dev_env(site_packages)

    result = _run_source_tree_pytest(
        python,
        cwd=project,
        env=env,
        site_packages=site_packages,
    )
    assert result.returncode != 0
    assert "_retracesoftware_editable_loader.py" not in result.stderr
    run_dir = _assert_failed_run_artifacts(project, result)

    runs = _run_source_tree_cli(
        python,
        ["runs"],
        cwd=project,
        env=env,
        site_packages=site_packages,
    )
    assert runs.returncode == 0, runs.stderr
    assert "test_fails" in runs.stdout

    agent_context = _run_source_tree_cli(
        python,
        ["agent-context", "--latest"],
        cwd=project,
        env=env,
        site_packages=site_packages,
    )
    assert agent_context.returncode == 0, agent_context.stderr
    assert "test_fails" in agent_context.stdout
    assert "recording_available: yes" in agent_context.stdout
    assert "recording_capture_method: full-session-clean-subprocess" in agent_context.stdout
    assert "capture_scope: full_session" in agent_context.stdout
    assert "failure_selection: first_failure" in agent_context.stdout

    inspect = _run_source_tree_cli(
        python,
        ["inspect", "--latest"],
        cwd=project,
        env=env,
        site_packages=site_packages,
        timeout=60,
    )
    assert inspect.returncode in {0, 1}
    if inspect.returncode == 1:
        assert (
            "replay/control returned no inspectable state" in inspect.stderr
            or "could not inspect this recording" in inspect.stderr
        )

    try:
        mcp = _run_source_tree_cli(
            python,
            ["mcp", "--latest"],
            cwd=project,
            env=env,
            site_packages=site_packages,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        mcp = None
    if mcp is not None:
        assert mcp.returncode == 0, mcp.stderr

    clean = _run_source_tree_cli(
        python,
        ["clean", "--latest", "--yes"],
        cwd=project,
        env=env,
        site_packages=site_packages,
    )
    assert clean.returncode == 0, clean.stderr
    assert not run_dir.exists()


@pytest.mark.skipif(
    os.environ.get("RETRACE_RUN_PYTEST_AGENT_EDITABLE_E2E") != "1",
    reason=(
        "opt-in editable-install smoke; set "
        "RETRACE_RUN_PYTEST_AGENT_EDITABLE_E2E=1 to run"
    ),
)
def test_pytest_agent_workflow_editable_install_with_path_spaces(tmp_path):
    project = tmp_path / "New project"
    _write_mini_project(project)

    venv_dir = project / ".venv"
    venv = _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=project)
    assert venv.returncode == 0, venv.stderr

    bin_dir = _venv_bin_dir(venv_dir)
    pip = str(bin_dir / "pip")
    pytest_bin = str(bin_dir / "pytest")
    retrace_bin = str(bin_dir / "retrace")

    install_project = _run([pip, "install", "-e", str(REPO_ROOT)], cwd=project, timeout=240)
    assert install_project.returncode == 0, install_project.stderr
    install_pytest = _run([pip, "install", "pytest"], cwd=project, timeout=120)
    assert install_pytest.returncode == 0, install_pytest.stderr

    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = "--retrace"
    result = _run([pytest_bin, "--retrace", "tests/test_example.py"], cwd=project, env=env, timeout=180)
    assert result.returncode != 0
    if "_retracesoftware_editable_loader.py" in result.stderr:
        pytest.fail(
            "editable Retrace install failed before pytest could run; "
            "see docs/dev/MESON_EDITABLE_BLOCKER.md; "
            f"stderr:\n{result.stderr}",
        )

    run_dir = _assert_failed_run_artifacts(project, result)

    runs = _run([retrace_bin, "runs"], cwd=project)
    assert runs.returncode == 0, runs.stderr
    assert "test_fails" in runs.stdout

    agent_context = _run([retrace_bin, "agent-context", "--latest"], cwd=project)
    assert agent_context.returncode == 0, agent_context.stderr
    assert "test_fails" in agent_context.stdout
    assert "recording_available: yes" in agent_context.stdout
    assert "recording_capture_method: full-session-clean-subprocess" in agent_context.stdout
    assert "capture_scope: full_session" in agent_context.stdout
    assert "failure_selection: first_failure" in agent_context.stdout

    inspect = _run([retrace_bin, "inspect", "--latest"], cwd=project, timeout=60)
    assert inspect.returncode in {0, 1}
    if inspect.returncode == 1:
        assert (
            "replay/control returned no inspectable state" in inspect.stderr
            or "could not inspect this recording" in inspect.stderr
        )

    try:
        mcp = _run([retrace_bin, "mcp", "--latest"], cwd=project, timeout=3)
    except subprocess.TimeoutExpired:
        mcp = None
    if mcp is not None:
        assert mcp.returncode == 0, mcp.stderr

    clean = _run([retrace_bin, "clean", "--latest", "--yes"], cwd=project)
    assert clean.returncode == 0, clean.stderr
    assert not run_dir.exists()
