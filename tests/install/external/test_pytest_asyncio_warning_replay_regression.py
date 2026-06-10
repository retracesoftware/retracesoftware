"""Regression for pytest-asyncio warning-summary replay divergence."""

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
    reason="pytest-asyncio warning summary currently desyncs terminal/time probes",
)
def test_pytest_asyncio_warning_summary_replays_cleanly(tmp_path: Path) -> None:
    pytest.importorskip("pytest_asyncio")

    files = {
        "tests/test_asyncio_warning.py": """
            import asyncio
            import pytest


            @pytest.mark.asyncio
            async def test_asyncio_sleep():
                await asyncio.sleep(0)
                assert True
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_asyncio_warning.py::test_asyncio_sleep",
            "-q",
            "--capture=sys",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_asyncio.plugin",
        },
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.get_terminal_size",
        "wrapped_function:time.time",
    )
    assert_successful_replay(record, replay, "1 passed")
