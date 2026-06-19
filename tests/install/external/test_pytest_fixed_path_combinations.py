"""Combined pytest regressions for the fixed replay paths."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tests.helpers import PYTHON, _completed_process_error, _run_for_pidfile, tail
from tests.install.external._pytest_replay_regression_helpers import (
    assert_replay_does_not_contain_signature,
    assert_successful_replay,
    clean_env,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
    write_files,
)


def test_pytest_cacheprovider_and_capfd_replay_together(tmp_path: Path) -> None:
    files = {
        "tests/test_cache_and_capture.py": """
            import os


            def test_cache_api_and_capfd(pytestconfig, capfd):
                cache = pytestconfig.cache
                seen = cache.get("retrace/combo", {"state": "recorded-miss"})
                os.write(1, b"combo fd stdout\\n")
                captured = capfd.readouterr()
                cache.set("retrace/combo", {"state": "written-during-record"})
                assert seen == {"state": "recorded-miss"}
                assert "combo fd stdout" in captured.out


            def test_default_capture_still_runs_after_cache_use(pytestconfig):
                print("default capture after cache")
                pytestconfig.cache.set("retrace/second", {"ok": True})
                assert pytestconfig.cache.get("retrace/second", None) == {"ok": True}
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_cache_and_capture.py", "-q", "--cache-clear"],
        env=env,
        replay_env=env,
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.getpid",
        "wrapped_function:time.time",
    )
    assert_successful_replay(record, replay, "2 passed")


def test_pytest_last_failed_cache_mode_replays_default_capture(
    tmp_path: Path,
) -> None:
    files = {
        "tests/test_last_failed.py": """
            import os


            def test_last_failed_cache_and_fd_capture(pytestconfig, capfd):
                assert pytestconfig.cache.get("cache/lastfailed", {}) == {}
                os.write(2, b"lf stderr fd\\n")
                captured = capfd.readouterr()
                assert "lf stderr fd" in captured.err
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_last_failed.py::test_last_failed_cache_and_fd_capture",
            "-q",
            "--lf",
        ],
        env=env,
        replay_env=env,
    )

    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")


@pytest.mark.xfail(
    reason=(
        "tracked in #64: retrace-venv autoenabled Python child subprocess "
        "records/extracts, but root pidfile replay exits early"
    ),
    strict=True,
)
def test_retrace_venv_pytest_child_process_cache_and_capfd_replays(
    tmp_path: Path,
) -> None:
    write_files(
        tmp_path,
        {
            "tests/test_retrace_venv_combo.py": """
                import os
                import subprocess
                import sys


                def test_child_process_cache_and_capture(pytestconfig, capfd):
                    seen = pytestconfig.cache.get("retrace/venv-combo", "recorded-miss")
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "import os; print('child-pid', os.getpid())",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    os.write(1, b"parent fd capture\\n")
                    captured = capfd.readouterr()
                    pytestconfig.cache.set("retrace/venv-combo", "written")
                    assert seen == "recorded-miss"
                    assert "child-pid" in proc.stdout
                    assert "parent fd capture" in captured.out
            """,
        },
    )

    recording = tmp_path / "trace.retrace"
    venv_dir = tmp_path / ".retrace-venv"
    install_env = clean_env(
        tmp_path,
        {
            "PYTHONPATH": minimal_project_pythonpath(tmp_path),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
    )
    install = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "venv",
            str(venv_dir),
            "--without-pip",
            "--system-site-packages",
        ],
        cwd=tmp_path,
        env=install_env,
        timeout=60,
    )
    assert install.returncode == 0, _completed_process_error(
        "create retrace venv",
        install,
    )
    retrace_python = venv_dir / "bin" / "python"

    record_env = install_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording.name
    record = _run_for_pidfile(
        [
            str(retrace_python),
            "-m",
            "pytest",
            "tests/test_retrace_venv_combo.py::test_child_process_cache_and_capture",
            "-q",
            "--tb=short",
            "--cache-clear",
        ],
        cwd=tmp_path,
        env=record_env,
        timeout=60,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert "1 passed" in record.stdout
    assert recording.exists()

    inspect_env = install_env.copy()
    inspect_env.pop("RETRACE_RECORDING", None)
    inspect_env.pop("RETRACE_CONFIG", None)
    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=inspect_env,
        timeout=60,
    )
    assert extract.returncode == 0, _completed_process_error("extract", extract)
    assert "parse preamble" not in extract.stdout + extract.stderr

    list_pids = _run_for_pidfile(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=inspect_env,
        timeout=60,
    )
    assert list_pids.returncode == 0, _completed_process_error("list_pids", list_pids)
    pids = [line for line in list_pids.stdout.splitlines() if line.strip()]
    assert len(pids) >= 2

    root_pid = pids[0]
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=inspect_env,
        timeout=60,
    )
    assert_replay_does_not_contain_signature(
        record,
        replay,
        "wrapped_function:posix.write",
        "b'blat'",
    )
    assert_successful_replay(record, replay, "1 passed")
