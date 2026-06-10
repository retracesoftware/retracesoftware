"""Regression for pytest cacheprovider replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.parametrize(
    "pytest_args",
    [
        ["tests/test_sample.py::test_sample_passes", "-q"],
        ["tests/test_sample.py::test_sample_passes", "-q", "--cache-clear"],
    ],
)
def test_pytest_default_cacheprovider_replay_finishes_after_passing_test(
    tmp_path: Path,
    pytest_args: list[str],
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
        pytest_args=pytest_args,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.getpid",
        "wrapped_function:time.time",
    )
    assert_successful_replay(record, replay, "1 passed")


def test_pytest_cache_api_replays_recorded_cache_state(tmp_path: Path) -> None:
    files = {
        "tests/test_cache_api.py": """
            def test_cache_api_uses_recorded_miss(pytestconfig):
                cache = pytestconfig.cache
                seen = cache.get("retrace/example", {"state": "recorded-miss"})
                cache.set("retrace/example", {"state": "written-during-record"})
                assert seen == {"state": "recorded-miss"}
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_cache_api.py::test_cache_api_uses_recorded_miss", "-q"],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert_successful_replay(record, replay, "1 passed")
