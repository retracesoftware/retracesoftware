"""Regression coverage for Python subclasses of random C-extension types."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail


@pytest.mark.xfail(
    strict=True,
    reason="random.Random subclasses hit issue #56 until method descriptor receiver unwrapping lands",
)
def test_random_subclass_records_and_replays_with_stacktraces(tmp_path: Path) -> None:
    script = tmp_path / "random_subclass_repro.py"
    script.write_text(
        """
import random
import _random


class LocalRandom(random.Random):
    pass


rng = LocalRandom()
assert isinstance(rng, _random.Random)
assert isinstance(rng, random.Random)
value = rng.getrandbits(8)
choice = rng.choice(["alpha", "beta", "gamma"])
print(f"random-subclass value={value} choice={choice}")
""",
        encoding="utf-8",
    )
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
            str(script),
        ],
        cwd=tmp_path,
        env=None,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=None,
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
    )
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\n"
        f"stderr:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout


def test_c_random_subclass_records_and_replays_with_stacktraces(tmp_path: Path) -> None:
    script = tmp_path / "c_random_subclass_repro.py"
    script.write_text(
        """
import _random


class DirectRandom(_random.Random):
    pass


rng = DirectRandom()
assert isinstance(rng, _random.Random)
value = rng.getrandbits(8)
print(f"c-random-subclass value={value}")
""",
        encoding="utf-8",
    )
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
            str(script),
        ],
        cwd=tmp_path,
        env=None,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=None,
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
    )
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\n"
        f"stderr:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
