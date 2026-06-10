"""Regression for pytest --import-mode=importlib replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest importlib collection mode currently exits during replay",
)
def test_pytest_importlib_import_mode_replays_collected_test(tmp_path: Path) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": """
            def value():
                return 42
        """,
        "tests/conftest.py": """
            import pytest


            @pytest.fixture
            def number():
                return 42
        """,
        "tests/test_importlib_mode.py": """
            from pkg.mod import value


            def test_value(number):
                assert value() == number
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_importlib_mode.py::test_value",
            "-q",
            "--capture=sys",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
        ],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert_successful_replay(record, replay, "1 passed")
