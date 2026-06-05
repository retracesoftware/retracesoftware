"""Regression for pytest-xdist multi-worker replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="pytest-xdist multi-worker replay currently hangs or diverges",
)
def test_pytest_xdist_multi_worker_replay_completes_external_work(tmp_path: Path) -> None:
    pytest.importorskip("xdist")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/work.py": """
            import subprocess
            import sys
            from pathlib import Path


            def child_line(tmp_path):
                path = Path(tmp_path) / "payload.txt"
                path.write_text("payload", encoding="utf-8")
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('child', int(time.time()) >= 0)",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return path.read_text(encoding="utf-8"), proc.stdout
        """,
        "tests/test_sample.py": """
            from samplepkg.work import child_line


            def test_worker_one(tmp_path):
                payload, stdout = child_line(tmp_path)
                assert payload == "payload"
                assert "child True" in stdout


            def test_worker_two(tmp_path):
                payload, stdout = child_line(tmp_path)
                assert payload == "payload"
                assert "child True" in stdout
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py",
            "-q",
            "--capture=sys",
            "-n",
            "2",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "xdist.plugin",
        },
        timeout=35,
    )

    assert_successful_replay(record, replay, "2 passed")
