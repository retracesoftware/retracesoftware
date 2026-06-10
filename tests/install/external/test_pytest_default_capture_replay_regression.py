"""Regression for pytest default fd capture replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
)


def test_pytest_default_capture_replay_does_not_misroute_fd_probe_writes(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pytest", minversion="9")

    files = {
        "tests/test_sample.py": """
            def test_sample_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_sample_passes",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": minimal_project_pythonpath(tmp_path),
        },
        replay_env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": minimal_project_pythonpath(tmp_path),
        },
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")
