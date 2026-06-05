"""Regression for pytest cacheprovider replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest cacheprovider currently records framework cache I/O out of order",
)
def test_pytest_default_cacheprovider_replay_finishes_after_passing_test(
    tmp_path: Path,
) -> None:
    files = {
        "tests/test_sample.py": """
            def test_sample_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_sample.py::test_sample_passes", "-q", "-s"],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert_successful_replay(record, replay, "1 passed")
