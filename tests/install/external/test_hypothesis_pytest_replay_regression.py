"""Regression for Hypothesis pytest plugin replay divergence."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="Hypothesis pytest integration currently desyncs filesystem stat/lstat probes",
)
def test_hypothesis_pytest_plugin_replays_derandomized_examples(tmp_path: Path) -> None:
    pytest.importorskip("hypothesis")

    workdir = Path(tempfile.mkdtemp(prefix="retrace-pytest-hypothesis-", dir="/tmp"))
    files = {
        "tests/test_hypothesis.py": """
            from hypothesis import given, settings, strategies as st


            @settings(max_examples=5, derandomize=True, database=None)
            @given(st.integers(min_value=0, max_value=10))
            def test_nonnegative(value):
                assert value >= 0
        """,
    }
    try:
        minimal_env = {"PYTHONPATH": minimal_project_pythonpath(workdir)}
        record, replay = record_extract_replay_pytest(
            workdir,
            files=files,
            pytest_args=[
                "tests/test_hypothesis.py::test_nonnegative",
                "-q",
                "--capture=sys",
                "-p",
                "no:cacheprovider",
            ],
            env={
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTEST_PLUGINS": "hypothesis.extra.pytestplugin",
                **minimal_env,
            },
            replay_env=minimal_env,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.stat",
        "wrapped_function:posix.lstat",
    )
    assert_successful_replay(record, replay, "1 passed")
