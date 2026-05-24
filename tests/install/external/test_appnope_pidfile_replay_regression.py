"""Regression for appnope pidfile replay shutdown divergence.

The dockertest trigger is small: import appnope on macOS, disable App Nap, run
and terminate one ``multiprocessing.Process``, then replay the extracted
PidFile.  On current broken builds, replay can finish with exit code 0 while
stderr still reports an ignored ``multiprocessing.util.Finalize`` callback that
tries to read another ``os.getpid()`` result after the trace is exhausted.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import textwrap

import pytest

from tests.helpers import PYTHON, retrace_env


_ATTEMPTS = 10


def _completed_process_error(
    label: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    return (
        f"{label} failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _without_retrace_debug_lines(stdout: str) -> str:
    lines = [
        line
        for line in stdout.splitlines()
        if not line.startswith("Retrace(")
    ]
    if stdout.endswith("\n"):
        return "\n".join(lines) + "\n"
    return "\n".join(lines)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="appnope only exercises the App Nap path on macOS",
)
def test_appnope_pidfile_replay_does_not_read_past_trace_at_finalize(
    tmp_path: Path,
):
    pytest.importorskip("appnope")

    env = retrace_env(os.environ, PYTHON)
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"

    script_source = textwrap.dedent(
        """
        import sys
        from multiprocessing import Process

        import appnope


        def dummy_task():
            return


        def test_appnope_with_process():
            if sys.platform == "darwin":
                appnope.nope()
                print("disabled app nap", flush=True)
            else:
                print(f"non-macOS platform ({sys.platform}); appnope treated as no-op", flush=True)

            p = Process(target=dummy_task)
            p.start()
            print("process started", flush=True)

            p.terminate()
            p.join(timeout=5)
            print("terminated", flush=True)


        if __name__ == "__main__":
            print("=== appnope_test ===", flush=True)
            test_appnope_with_process()
        """
    )

    for attempt in range(1, _ATTEMPTS + 1):
        attempt_dir = tmp_path / f"attempt-{attempt}"
        attempt_dir.mkdir()
        script = attempt_dir / "appnope_pidfile_repro.py"
        script.write_text(script_source, encoding="utf-8")
        recording = attempt_dir / "trace.retrace"

        record = subprocess.run(
            [
                PYTHON,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
                "--",
                script.name,
            ],
            cwd=attempt_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert record.returncode == 0, _completed_process_error(
            f"record attempt {attempt}",
            record,
        )
        assert "disabled app nap" in record.stdout
        assert "process started" in record.stdout
        assert "terminated" in record.stdout

        extract = subprocess.run(
            [str(recording), "--extract"],
            cwd=attempt_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert extract.returncode == 0, _completed_process_error(
            f"extract attempt {attempt}",
            extract,
        )

        list_pids = subprocess.run(
            [
                PYTHON,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
                "--list_pids",
            ],
            cwd=attempt_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert list_pids.returncode == 0, _completed_process_error(
            f"list_pids attempt {attempt}",
            list_pids,
        )
        root_pid = list_pids.stdout.splitlines()[0]
        pidfile = attempt_dir / "trace.d" / f"{root_pid}.bin"
        assert pidfile.exists()

        replay = subprocess.run(
            [str(pidfile)],
            cwd=attempt_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=replay_env,
        )
        assert replay.returncode == 0, _completed_process_error(
            f"pidfile replay attempt {attempt}",
            replay,
        )
        assert replay.stdout == _without_retrace_debug_lines(record.stdout)
        assert "Exception ignored in: <Finalize object" not in replay.stderr, (
            f"pidfile replay attempt {attempt} ran a weakref finalizer after "
            f"the replay trace was exhausted\nstdout:\n{replay.stdout}\n"
            f"stderr:\n{replay.stderr}"
        )
        assert "Could not read: 1 bytes from tracefile" not in replay.stderr, (
            f"pidfile replay attempt {attempt} read past EOF in the replay "
            f"trace\nstdout:\n{replay.stdout}\nstderr:\n{replay.stderr}"
        )
