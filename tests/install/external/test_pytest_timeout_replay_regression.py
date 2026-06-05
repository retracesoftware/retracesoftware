"""Regression for pytest-timeout signal replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest-timeout signal interruption is not yet replayed deterministically",
)
def test_pytest_timeout_signal_failure_replays_same_timeout(tmp_path: Path) -> None:
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

    assert record.returncode != 0
    combined = replay.stdout + replay.stderr
    assert_replay_does_not_contain_signature(
        record,
        replay,
        "Checkpoint difference:",
        "_signal.setitimer",
    )
    assert replay.returncode != 0
    assert "Timeout" in combined
    assert "Checkpoint difference:" not in combined
