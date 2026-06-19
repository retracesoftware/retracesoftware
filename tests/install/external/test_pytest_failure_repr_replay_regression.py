"""Regression coverage for pytest failure-report formatting during replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import tail
from tests.install.external._pytest_replay_regression_helpers import (
    record_extract_replay_pytest,
)


@pytest.mark.parametrize("stacktraces", [False, True], ids=["plain", "stacktraces"])
def test_pytest_pandas_failure_repr_replays_original_assertion(
    tmp_path: Path,
    stacktraces: bool,
) -> None:
    pytest.importorskip("pandas")

    files = {
        "tests/test_pandas_failure.py": """
            import pandas as pd
            from pandas.testing import assert_frame_equal


            def test_frame_failure():
                left = pd.DataFrame({"amount": list(range(300))})
                right = pd.DataFrame({"amount": list(range(300))})
                right.loc[249, "amount"] = -1
                assert_frame_equal(left, right)
        """,
    }

    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_pandas_failure.py::test_frame_failure",
            "-q",
            "--tb=short",
        ],
        stacktraces=stacktraces,
        timeout=90,
    )

    assert record.returncode == 1, (
        f"record should capture the intended pytest failure\n"
        f"stdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )

    combined = replay.stdout + replay.stderr
    assert replay.returncode == 1, (
        f"replay should reproduce the pytest failure, not diverge\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    assert "DataFrame.iloc" in combined
    assert "expected str, bytes or os.PathLike object, not stat_result" not in combined
    assert "Checkpoint difference:" not in combined
    assert "bind marker returned" not in combined
