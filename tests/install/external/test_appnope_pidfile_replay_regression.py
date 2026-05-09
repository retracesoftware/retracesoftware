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


_ROOT = Path(__file__).resolve().parents[3]
_ATTEMPTS = 10


def _local_pythonpath() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = [str((_ROOT / "src").resolve())]
    for rel in (
        f"build/{build_tag}/cpp/utils",
        f"build/{build_tag}/cpp/stream",
        f"build/{build_tag}/cpp/functional",
        f"build/{build_tag}/cpp/cursor",
    ):
        path = _ROOT / rel
        if path.exists():
            entries.append(str(path.resolve()))
    return os.pathsep.join(entries)


def _editable_skip() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = []
    local_build = _ROOT / "build" / build_tag
    if local_build.exists():
        entries.append(str(local_build.resolve()))
    utils_build = _ROOT.parent / "utils" / "build" / build_tag
    if utils_build.exists():
        entries.append(str(utils_build.resolve()))
    return os.pathsep.join(entries)


def _completed_process_error(
    label: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    return (
        f"{label} failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="appnope only exercises the App Nap path on macOS",
)
def test_appnope_pidfile_replay_does_not_read_past_trace_at_finalize(
    tmp_path: Path,
):
    pytest.importorskip("appnope")

    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath()
    # The test invokes ``python -m retracesoftware`` explicitly.  Leaving
    # RETRACE_CONFIG in the inherited environment lets spawn-based
    # multiprocessing children auto-enable and race verbose writer output into
    # the parent's captured stdout before ``terminate()`` wins.
    env.pop("RETRACE_CONFIG", None)

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
                sys.executable,
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
                sys.executable,
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
        assert replay.stdout == record.stdout
        assert "Exception ignored in: <Finalize object" not in replay.stderr, (
            f"pidfile replay attempt {attempt} ran a weakref finalizer after "
            f"the replay trace was exhausted\nstdout:\n{replay.stdout}\n"
            f"stderr:\n{replay.stderr}"
        )
        assert "Could not read: 1 bytes from tracefile" not in replay.stderr, (
            f"pidfile replay attempt {attempt} read past EOF in the replay "
            f"trace\nstdout:\n{replay.stdout}\nstderr:\n{replay.stderr}"
        )
