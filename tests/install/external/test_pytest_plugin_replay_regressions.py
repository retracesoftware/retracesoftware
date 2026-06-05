"""Known pytest integration replay regressions.

These tests intentionally exercise pytest framework/plugin behavior through
record/extract/replay. They are xfailed while the corresponding GitHub issues
are open; when a fix lands, the matching xfail should flip to XPASS and force
the test to be unmarked.
"""

from __future__ import annotations

import os
from pathlib import Path
import textwrap

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, local_pythonpath, tail


TIMEOUT = 45


def _clean_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), local_pythonpath()])
    for key in (
        "RETRACE_CONFIG",
        "RETRACE_INODE",
        "RETRACE_RECORDING",
        "RETRACE_SKIP_CHECKSUMS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")


def _record_extract_replay_pytest(
    tmp_path: Path,
    *,
    files: dict[str, str],
    pytest_args: list[str],
    env: dict[str, str] | None = None,
    timeout: int = TIMEOUT,
):
    _write_files(tmp_path, files)
    recording = tmp_path / "trace.retrace"
    record_env = _clean_env(tmp_path, env)

    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            "-m",
            "pytest",
            *pytest_args,
        ],
        cwd=tmp_path,
        env=record_env,
        timeout=timeout,
    )
    assert recording.exists(), (
        f"recording was not created\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}"
    )

    replay_env = _clean_env(tmp_path)
    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=replay_env,
        timeout=timeout,
    )
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
        env=replay_env,
        timeout=timeout,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay = _run_for_pidfile(
        [str(pidfile)],
        cwd=tmp_path,
        env=replay_env,
        timeout=timeout,
    )
    return record, replay


def _assert_successful_replay(record, replay, expected: str) -> None:
    assert replay.returncode == 0, (
        f"pytest replay diverged (exit {replay.returncode})\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    combined = replay.stdout + replay.stderr
    assert expected in combined
    assert "Checkpoint difference:" not in combined
    assert "Could not read:" not in combined
    assert "bind marker returned" not in combined


@pytest.mark.xfail(
    strict=True,
    reason="pytest plugin autoload currently leaks plugin control-plane thread state into replay",
)
def test_pytest_plugin_autoload_replay_handles_plugin_control_plane_threads(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pytest_rerunfailures")

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
    record, replay = _record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_sample_passes",
            "-q",
            "--capture=sys",
            "-p",
            "no:cacheprovider",
        ],
    )

    _assert_successful_replay(record, replay, "1 passed")


@pytest.mark.xfail(
    strict=True,
    reason="pytest cacheprovider currently records framework cache I/O out of order",
)
def test_pytest_default_cacheprovider_replay_finishes_after_passing_test(
    tmp_path: Path,
) -> None:
    files = {
        "tests/test_sample.py": """
            def test_sample_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = _record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_sample.py::test_sample_passes", "-q", "-s"],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    _assert_successful_replay(record, replay, "1 passed")


@pytest.mark.xfail(
    strict=True,
    reason="pytest-cov/coverage tracing currently corrupts replay path/stat ordering",
)
def test_pytest_cov_replay_keeps_coverage_path_state_aligned(tmp_path: Path) -> None:
    pytest.importorskip("pytest_cov")
    pytest.importorskip("coverage")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def add(left, right):
                return left + right
        """,
        "tests/test_sample.py": """
            from samplepkg.calc import add


            def test_add():
                assert add(2, 3) == 5
        """,
    }
    record, replay = _record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_add",
            "-q",
            "-s",
            "--cov=samplepkg",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_cov.plugin",
        },
    )

    _assert_successful_replay(record, replay, "1 passed")


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
    record, replay = _record_extract_replay_pytest(
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

    _assert_successful_replay(record, replay, "2 passed")


@pytest.mark.xfail(
    strict=True,
    reason="pytest-forked currently misaligns fork/session cleanup replay messages",
)
def test_pytest_forked_replay_finishes_child_isolated_test(tmp_path: Path) -> None:
    pytest.importorskip("pytest_forked")

    files = {
        "tests/test_sample.py": """
            def test_forked_passes():
                assert 2 + 2 == 4
        """,
    }
    record, replay = _record_extract_replay_pytest(
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

    _assert_successful_replay(record, replay, "1 passed")


@pytest.mark.xfail(
    strict=True,
    reason="pytest-timeout signal interruption is not yet replayed deterministically",
)
def test_pytest_timeout_signal_failure_replays_same_timeout(tmp_path: Path) -> None:
    pytest.importorskip("pytest_timeout")

    files = {
        "tests/test_sample.py": """
            import time


            def test_times_out():
                time.sleep(1.0)
        """,
    }
    record, replay = _record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_times_out",
            "-q",
            "-s",
            "--tb=line",
            "--timeout=0.2",
            "-p",
            "no:cacheprovider",
        ],
        env={
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "pytest_timeout",
        },
    )

    assert record.returncode != 0
    combined = replay.stdout + replay.stderr
    assert replay.returncode != 0
    assert "Timeout" in combined
    assert "Checkpoint difference:" not in combined


@pytest.mark.xfail(
    strict=True,
    reason="pytest fd capture setup/teardown currently pollutes replay ordering",
)
def test_pytest_capfd_replay_keeps_fd_capture_and_summary_ordered(
    tmp_path: Path,
) -> None:
    files = {
        "tests/test_sample.py": """
            import os


            def test_capfd_reads_fd_output(capfd):
                os.write(1, b"fd stdout\\\\n")
                captured = capfd.readouterr()
                assert "fd stdout" in captured.out
        """,
    }
    record, replay = _record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_sample.py::test_capfd_reads_fd_output",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    _assert_successful_replay(record, replay, "1 passed")
