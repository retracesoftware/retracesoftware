"""Regression coverage for common single-process pytest plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail
from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    clean_env,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
    write_files,
)


def test_pytest_mock_and_randomly_plugins_replay_single_process_run(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pytest_mock")
    pytest.importorskip("pytest_randomly")

    files = {
        "tests/test_mock_randomly.py": """
            def test_mocker_fixture_under_randomly(mocker):
                callback = mocker.Mock(return_value={"value": 42})
                assert callback()["value"] == 42
                callback.assert_called_once_with()
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_mock_randomly.py::test_mocker_fixture_under_randomly",
            "-q",
            "--randomly-seed=12345",
            "-p",
            "pytest_mock",
            "-p",
            "pytest_randomly",
        ],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_coverage_run_pytest_mock_randomly_cache_seed_replays(
    tmp_path: Path,
) -> None:
    pytest.importorskip("coverage")
    pytest.importorskip("pytest_mock")
    pytest.importorskip("pytest_randomly")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def total(values):
                return sum(values)
        """,
        "tests/test_coverage_mock_randomly.py": """
            from samplepkg.calc import total


            def test_coverage_mock_randomly_one(mocker):
                callback = mocker.Mock(return_value=[10, 20, 12])
                assert total(callback()) == 42


            def test_coverage_mock_randomly_two(mocker):
                callback = mocker.Mock(return_value="retrace")
                assert callback().upper() == "RETRACE"
        """,
    }
    write_files(tmp_path, files)
    recording = tmp_path / "trace.retrace"
    env = clean_env(
        tmp_path,
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_mock,pytest_randomly",
            "PYTHONPATH": minimal_project_pythonpath(tmp_path),
        },
    )

    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_coverage_mock_randomly.py",
            "-q",
            "--randomly-seed=12345",
        ],
        cwd=tmp_path,
        env=env,
        timeout=90,
    )
    assert recording.exists(), (
        f"recording was not created\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}"
    )

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=env,
        timeout=90,
    )
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\n"
        f"stderr:\n{tail(extract.stderr)}"
    )

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=env,
        timeout=90,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=env,
        timeout=90,
    )

    assert_successful_replay(record, replay, "2 passed")
