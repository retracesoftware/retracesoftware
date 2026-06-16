"""Regression for pytest-forked replay divergence."""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    clean_env,
    record_extract_replay_pytest,
)
from tests.helpers import PYTHON, _run_for_pidfile, tail


def test_py_process_forkedfunc_result_replays_without_temp_file_state(
    tmp_path: Path,
) -> None:
    pytest.importorskip("py._process.forkedfunc")

    script = tmp_path / "forkedfunc_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import py


            def child():
                return b"hello-report"


            forked = py.process.ForkedFunc(child)
            result = forked.waitfinish()
            print("RESULT", result.exitstatus, result.retval, result.out, result.err, flush=True)
            assert result.exitstatus == 0
            assert result.retval == b"hello-report"
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = clean_env(tmp_path)
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
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )

    extract = _run_for_pidfile([str(recording), "--extract"], cwd=tmp_path, env=env)
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
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
        env=env,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=env,
    )
    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\nstderr:\n{tail(replay.stderr)}"
    )
    assert "RESULT 0 b'hello-report'" in replay.stdout


def test_pytest_forked_replay_finishes_child_isolated_test(tmp_path: Path) -> None:
    pytest.importorskip("pytest_forked")

    files = {
        "tests/test_sample.py": """
            def test_forked_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_forked_passes",
            "-q",
            "--capture=sys",
            "--forked",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_forked",
        },
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")


def test_pytest_forked_replay_preserves_child_failure_report(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pytest_forked")

    files = {
        "tests/test_sample.py": """
            def test_forked_fails():
                value = 2 + 2
                assert value == 5
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_forked_fails",
            "-q",
            "--capture=sys",
            "--forked",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_forked",
        },
    )

    assert record.returncode == 1, (
        f"record should fail\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert replay.returncode == 1, (
        f"replay should reproduce pytest failure\n"
        f"stdout:\n{tail(replay.stdout)}\nstderr:\n{tail(replay.stderr)}"
    )
    combined = replay.stdout + replay.stderr
    assert "FAILED tests/test_sample.py::test_forked_fails" in combined
    assert "assert 4 == 5" in combined
    assert "Checkpoint difference:" not in combined
    assert "EOFError" not in combined
