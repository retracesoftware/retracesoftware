"""Regression coverage for common single-process pytest plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
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

