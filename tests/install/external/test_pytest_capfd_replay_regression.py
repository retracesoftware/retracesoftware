"""Regression for pytest fd-level capture replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest fd capture setup/teardown currently pollutes replay ordering",
)
def test_pytest_capfd_replay_keeps_fd_capture_and_summary_ordered(
    tmp_path: Path,
) -> None:
    files = {
        "tests/test_sample.py": """
            import os


            def test_capfd_reads_fd_output(capfd):
                os.write(1, b"fd stdout\\n")
                captured = capfd.readouterr()
                assert "fd stdout" in captured.out
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_capfd_reads_fd_output",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert_successful_replay(record, replay, "1 passed")
