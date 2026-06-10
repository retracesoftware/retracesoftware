"""Regression for pytest-html report generation replay divergence."""

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
    reason="pytest-html report generation currently desyncs report filesystem probes",
)
def test_pytest_html_report_generation_replays_cleanly(tmp_path: Path) -> None:
    pytest.importorskip("pytest_html")
    pytest.importorskip("pytest_metadata")

    workdir = Path(tempfile.mkdtemp(prefix="retrace-pytest-html-", dir="/tmp"))
    files = {
        "tests/test_sample.py": """
            def test_sample_passes():
                assert 2 + 2 == 4
        """,
    }
    try:
        minimal_env = {"PYTHONPATH": minimal_project_pythonpath(workdir)}
        record, replay = record_extract_replay_pytest(
            workdir,
            files=files,
            pytest_args=[
                "tests/test_sample.py::test_sample_passes",
                "-q",
                "--capture=sys",
                "--html=report.html",
                "--self-contained-html",
                "-p",
                "no:cacheprovider",
                "-p",
                "pytest_metadata.plugin",
                "-p",
                "pytest_html.plugin",
            ],
            env={
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                **minimal_env,
            },
            replay_env=minimal_env,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    assert "Generated html report" in record.stdout
    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.stat",
        "wrapped_function:posix.listdir",
    )
    assert_successful_replay(record, replay, "1 passed")
