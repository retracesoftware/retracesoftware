"""Regression coverage for coverage.py run/replay hangs."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail


TIMEOUT = 20


def _record_extract_replay_script(
    tmp_path: Path,
    script_name: str,
    script_source: str,
    record_args: list[str],
):
    script = tmp_path / script_name
    script.write_text(script_source, encoding="utf-8")
    recording = tmp_path / "trace.retrace"

    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            *record_args,
        ],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )
    assert recording.exists()

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\n"
        f"stderr:\n{tail(extract.stderr)}"
    )

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    return record, replay


@pytest.mark.xfail(
    strict=True,
    reason="coverage run currently hangs replay after coverage tracing re-enters Retrace dispatcher",
)
def test_coverage_run_plain_script_replays_to_completion(tmp_path: Path) -> None:
    pytest.importorskip("coverage")

    record, replay = _record_extract_replay_script(
        tmp_path,
        "app.py",
        "print('coverage-script-value=42')\n",
        ["-m", "coverage", "run", "app.py"],
    )

    assert "coverage-script-value=42" in record.stdout
    assert replay.returncode == 0, (
        f"coverage replay failed or timed out\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    assert "coverage-script-value=42" in replay.stdout


@pytest.mark.xfail(
    strict=True,
    reason="coverage run -m pytest currently fails during replay under coverage tracing",
)
def test_coverage_run_pytest_replays_to_completion(tmp_path: Path) -> None:
    pytest.importorskip("coverage")

    record, replay = _record_extract_replay_script(
        tmp_path,
        "test_covered_sample.py",
        """
from pathlib import Path


def test_covered_sample(tmp_path):
    value_file = Path(tmp_path) / "value.txt"
    value_file.write_text("42", encoding="utf-8")
    assert int(value_file.read_text(encoding="utf-8")) == 42
""",
        ["-m", "coverage", "run", "-m", "pytest", "-q", "test_covered_sample.py"],
    )

    assert "1 passed" in record.stdout
    assert replay.returncode == 0, (
        f"coverage pytest replay failed or timed out\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    assert "1 passed" in replay.stdout


@pytest.mark.xfail(
    strict=True,
    reason="coverage shutdown currently materializes an invalid sqlite connection during replay",
)
def test_coverage_api_shutdown_replays_without_sqlite_stub_error(tmp_path: Path) -> None:
    pytest.importorskip("coverage")

    record, replay = _record_extract_replay_script(
        tmp_path,
        "coverage_api.py",
        """
import coverage

cov = coverage.Coverage(data_file=None)
cov.start()
try:
    value = sum([10, 20, 12])
finally:
    cov.stop()
    cov.save()
print(f"value={value}")
""",
        ["coverage_api.py"],
    )

    assert "value=42" in record.stdout
    assert replay.returncode == 0
    assert "value=42" in replay.stdout
    assert "Exception ignored in atexit callback" not in replay.stderr
    assert "Base Connection.__init__ not called" not in replay.stderr
