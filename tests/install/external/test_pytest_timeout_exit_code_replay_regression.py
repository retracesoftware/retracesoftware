"""Regression for pytest-timeout replay exit-code preservation."""

from __future__ import annotations

from pathlib import Path

from tests.install.external._pytest_replay_regression_helpers import (
    record_extract_replay_pytest,
)


def test_pytest_timeout_replay_preserves_failed_exit_code(tmp_path: Path) -> None:
    import pytest

    pytest.importorskip("pytest_timeout")

    files = {
        "tests/test_sample.py": """
            import time


            def test_times_out():
                time.sleep(1.0)
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_times_out",
            "-q",
            "-s",
            "--tb=line",
            "--timeout=0.2",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_timeout",
        },
    )

    combined = replay.stdout + replay.stderr
    assert record.returncode == 1
    assert "Timeout (>0.2s) from pytest-timeout" in combined
    assert "1 failed" in combined
    assert "Checkpoint difference:" not in combined
    assert replay.returncode == record.returncode
