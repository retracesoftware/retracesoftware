"""Regression for pytest-rerunfailures actual rerun replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    record_extract_replay_pytest,
)

def test_pytest_rerunfailures_actual_rerun_replays_cleanly(tmp_path: Path) -> None:
    pytest.importorskip("pytest_rerunfailures")

    files = {
        "tests/test_rerun.py": """
            from pathlib import Path


            def test_reruns_once():
                path = Path("attempt.txt")
                attempt = int(path.read_text()) if path.exists() else 0
                path.write_text(str(attempt + 1), encoding="utf-8")
                assert attempt >= 1
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_rerun.py::test_reruns_once",
            "-q",
            "--capture=sys",
            "--reruns",
            "1",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_rerunfailures",
        },
    )

    assert "1 rerun" in record.stdout
    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.getcwd",
        "wrapped_function:time.time",
    )
    assert_successful_replay(record, replay, "1 passed")
