"""Regression for pytest-forked replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest-forked currently misaligns fork/session cleanup replay messages",
)
def test_pytest_forked_replay_finishes_child_isolated_test(tmp_path: Path) -> None:
    pytest.importorskip("pytest_forked")

    files = {
        "tests/test_sample.py": """
            def test_forked_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_forked_passes",
            "-q",
            "--capture=sys",
            "--forked",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_forked",
        },
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")
