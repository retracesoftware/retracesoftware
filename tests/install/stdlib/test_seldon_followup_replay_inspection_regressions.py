"""Regression coverage for Seldon-derived replay inspection follow-ups.

These cases were originally found while reducing a Seldon/multiprocess
shutdown failure.  They are intentionally kept separate from the baseline
framed-recording inspection bridge covered in ``tests/test_agent_context_cli``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from retracesoftware import agent_inspect


TIMEOUT = 45


def _run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env["PYTHONFAULTHANDLER"] = "1"
    if env:
        run_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=run_env,
        text=True,
        capture_output=True,
        timeout=TIMEOUT,
    )


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    script = tmp_path / name
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    return script


def _record_script(
    tmp_path: Path,
    script: Path,
    *,
    trace_shutdown: bool = False,
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    recording = tmp_path / f"{script.stem}.retrace"
    cmd = [
        sys.executable,
        "-m",
        "retracesoftware",
        "--recording",
        str(recording),
        "--format",
        "binary",
    ]
    if trace_shutdown:
        cmd.append("--trace_shutdown")
    cmd.extend(["--", str(script)])
    result = _run(cmd, cwd=tmp_path)
    return recording, result


def _list_pids(recording: Path, tmp_path: Path) -> list[str]:
    result = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _inspect_all_pids(recording: Path, tmp_path: Path) -> list[dict]:
    reports = []
    for pid in _list_pids(recording, tmp_path):
        reports.append(
            agent_inspect.inspect_recording(
                str(recording),
                pid=pid,
                timeout_seconds=TIMEOUT,
            )
        )
    return reports


def _has_exception(reports: list[dict], exc_type: str, message: str) -> bool:
    for report in reports:
        exception = report.get("exception") or {}
        if exception.get("type") == exc_type and message in str(exception.get("message")):
            return True
    return False


def test_pyio_dill_and_multiprocess_imports_record_cleanly(tmp_path: Path) -> None:
    pytest.importorskip("dill")
    pytest.importorskip("multiprocess")
    script = _write_script(
        tmp_path,
        "import_pyio_stack.py",
        """
        import _pyio
        import dill
        import multiprocess

        print("imported", _pyio.__name__, dill.__version__, multiprocess.__version__, flush=True)
        """,
    )

    recording, result = _record_script(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "imported _pyio" in result.stdout
    assert "TypeError: Can only register classes" not in result.stderr


def test_fork_child_unhandled_exception_is_inspectable_by_child_pid(tmp_path: Path) -> None:
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is not available")
    script = _write_script(
        tmp_path,
        "fork_child_unhandled.py",
        """
        import os
        import sys

        marker = {"where": "fork-child"}
        pid = os.fork()
        if pid == 0:
            raise RuntimeError(f"child failure marker={marker}")

        os.waitpid(pid, 0)
        print("parent exited normally", flush=True)
        sys.exit(0)
        """,
    )

    recording, result = _record_script(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "child failure marker={'where': 'fork-child'}" in result.stderr
    assert _has_exception(
        _inspect_all_pids(recording, tmp_path),
        "RuntimeError",
        "child failure marker={'where': 'fork-child'}",
    )


def test_atexit_exception_is_inspectable_when_shutdown_is_traced(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "atexit_exception.py",
        """
        import atexit

        marker = {"where": "atexit"}

        def boom():
            raise RuntimeError(f"atexit failure marker={marker}")

        atexit.register(boom)
        print("main exited normally", flush=True)
        """,
    )

    recording, result = _record_script(tmp_path, script, trace_shutdown=True)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "atexit failure marker={'where': 'atexit'}" in result.stderr
    assert _has_exception(
        _inspect_all_pids(recording, tmp_path),
        "RuntimeError",
        "atexit failure marker={'where': 'atexit'}",
    )


def test_fork_child_atexit_exception_is_inspectable_by_child_pid(tmp_path: Path) -> None:
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is not available")
    script = _write_script(
        tmp_path,
        "fork_child_atexit_exception.py",
        """
        import atexit
        import os
        import sys

        marker = {"where": "fork-child-atexit"}

        def boom():
            raise RuntimeError(f"child atexit failure marker={marker}")

        pid = os.fork()
        if pid == 0:
            atexit.register(boom)
            print("child main exited normally", flush=True)
            sys.exit(0)

        os.waitpid(pid, 0)
        print("parent exited normally", flush=True)
        sys.exit(0)
        """,
    )

    recording, result = _record_script(tmp_path, script, trace_shutdown=True)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "child atexit failure marker={'where': 'fork-child-atexit'}" in result.stderr
    assert _has_exception(
        _inspect_all_pids(recording, tmp_path),
        "RuntimeError",
        "child atexit failure marker={'where': 'fork-child-atexit'}",
    )


def test_multiprocess_inherited_children_shutdown_assertion_is_inspectable(
    tmp_path: Path,
) -> None:
    pytest.importorskip("multiprocess")
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is not available")
    script = _write_script(
        tmp_path,
        "multiprocess_inherited_children.py",
        """
        import os
        import sys
        import time
        from multiprocess import Process

        def worker():
            time.sleep(3)

        if __name__ == "__main__":
            process = Process(target=worker)
            process.start()

            forked = os.fork()
            if forked == 0:
                print(
                    f"forked worker inherited parent_pid={process._parent_pid}",
                    flush=True,
                )
                sys.exit(0)

            os.waitpid(forked, 0)
            process.terminate()
            process.join(timeout=5)
            print("parent exited normally", flush=True)
        """,
    )

    recording, result = _record_script(tmp_path, script, trace_shutdown=True)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "AssertionError: can only join a child process" in result.stderr
    assert _has_exception(
        _inspect_all_pids(recording, tmp_path),
        "AssertionError",
        "can only join a child process",
    )


@pytest.mark.parametrize(
    ("script_name", "source"),
    [
        (
            "fork_child_signal_pause.py",
            """
            import os
            import signal
            import sys
            import time

            pid = os.fork()
            if pid == 0:
                print("child entering pause", flush=True)
                signal.pause()
                sys.exit(0)

            time.sleep(0.2)
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
            print("parent exited normally", flush=True)
            """,
        ),
        (
            "fork_child_busy_loop.py",
            """
            import os
            import signal
            import sys
            import time

            pid = os.fork()
            if pid == 0:
                print("child entering loop", flush=True)
                while True:
                    pass
                sys.exit(0)

            time.sleep(0.2)
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
            print("parent exited normally", flush=True)
            """,
        ),
    ],
)
def test_fork_child_signal_pause_and_busy_loop_record_without_typeerror(
    tmp_path: Path,
    script_name: str,
    source: str,
) -> None:
    if not hasattr(os, "fork"):
        pytest.skip("os.fork is not available")
    script = _write_script(
        tmp_path,
        script_name,
        source,
    )

    recording, result = _record_script(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert recording.exists()
    assert "TypeError: 'int' object is not callable" not in result.stderr
