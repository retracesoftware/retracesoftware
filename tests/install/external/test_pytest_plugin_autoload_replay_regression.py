"""Regression for pytest plugin autoload replay shutdown divergence."""

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
    reason="normal pytest plugin autoload currently diverges during replay",
)
def test_pytest_plugin_autoload_replay_reaches_passing_test(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pytest_rerunfailures")
    pytest.importorskip("xdist")

    files = {
        "pytest_fake_retrace_plugin.py": """
            def pytest_configure(config):
                config.addinivalue_line("markers", "fake_retrace: fake plugin")
        """,
        "fake_retrace_plugin-1.0.dist-info/METADATA": """
            Metadata-Version: 2.1
            Name: fake-retrace-plugin
            Version: 1.0
        """,
        "fake_retrace_plugin-1.0.dist-info/entry_points.txt": """
            [pytest11]
            fake_retrace_plugin = pytest_fake_retrace_plugin
        """,
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
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")
