"""Regression for pytest-cov replay divergence."""

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
    reason="pytest-cov/coverage tracing currently corrupts replay path/stat ordering",
)
def test_pytest_cov_replay_keeps_coverage_path_state_aligned(tmp_path: Path) -> None:
    pytest.importorskip("pytest_cov")
    pytest.importorskip("coverage")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def add(left, right):
                return left + right
        """,
        "tests/test_sample.py": """
            from samplepkg.calc import add


            def test_add():
                assert add(2, 3) == 5
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_add",
            "-q",
            "-s",
            "--cov=samplepkg",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_cov.plugin",
        },
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.getcwd",
        "wrapped_function:time.time",
    )
    assert_successful_replay(record, replay, "1 passed")
