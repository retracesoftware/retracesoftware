"""Regressions for replay routing across high-contention threading primitives.

These are deliberately narrower than the Flask/Datasette server failures: they
keep only stdlib threading primitives plus ``queue.Queue`` so a replay failure
points at thread scheduling/message routing rather than web framework behavior.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


TIMEOUT = 30


def _completed_process_error(label: str, proc: subprocess.CompletedProcess[str]) -> str:
    return (
        f"{label} failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def _tail(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            args,
            124,
            stdout=stdout,
            stderr=stderr + f"\nreplay timed out after {timeout}s\n",
        )


def _local_pythonpath() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    existing = os.environ.get("PYTHONPATH")
    paths = [str(repo_root)]
    if existing:
        paths.append(existing)
    return os.pathsep.join(paths)


def _editable_skip() -> str:
    return os.environ.get("MESONPY_EDITABLE_SKIP", "1")


def _record_and_replay_pth_script(
    *,
    tmp_path: Path,
    script_name: str,
    script_source: str,
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    script = tmp_path / script_name
    script.write_text(textwrap.dedent(script_source), encoding="utf-8")

    recording_name = "trace.retrace"
    recording = tmp_path / recording_name

    install_env = os.environ.copy()
    install_env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    install_env["PYTHONPATH"] = _local_pythonpath()
    install_env.pop("RETRACE_RECORDING", None)
    install_env.pop("RETRACE_CONFIG", None)
    install_env.pop("RETRACE_SKIP_CHECKSUMS", None)

    install = _run(
        [sys.executable, "-m", "retracesoftware", "install"],
        cwd=tmp_path,
        env=install_env,
    )
    assert install.returncode == 0, _completed_process_error(
        "install auto-enable",
        install,
    )

    record_env = install_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording_name

    record = _run([sys.executable, script.name], cwd=tmp_path, env=record_env)
    assert record.returncode == 0, (
        f"record failed for {script_name}\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert recording.exists()

    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=install_env)
    assert extract.returncode == 0, _completed_process_error("extract", extract)

    list_pids = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=install_env,
    )
    assert list_pids.returncode == 0, _completed_process_error(
        "list_pids",
        list_pids,
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = install_env.copy()
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)
    return record, replay


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PidFile replay can overfill the dispatcher when Barrier releases "
        "several replay threads that then contend on Queue notifications"
    ),
)
def test_threading_barrier_queue_pth_pidfile_replay_does_not_overfill_dispatcher(
    tmp_path: Path,
):
    record, replay = _record_and_replay_pth_script(
        tmp_path=tmp_path,
        script_name="threading_barrier_queue_repro.py",
        script_source="""
            import queue
            import threading


            def main():
                print("=== threading_barrier_queue_repro ===", flush=True)
                barrier = threading.Barrier(7)
                results = queue.Queue()

                def worker(index):
                    for round_no in range(10):
                        barrier.wait()
                        results.put((round_no, index, index * index))

                threads = [
                    threading.Thread(target=worker, args=(index,))
                    for index in range(6)
                ]
                for thread in threads:
                    thread.start()

                for _ in range(10):
                    barrier.wait()
                for thread in threads:
                    thread.join()

                values = sorted(results.get() for _ in range(60))
                assert len(values) == 60
                assert values[0] == (0, 0, 0)
                assert values[-1] == (9, 5, 25)
                print(f"values={len(values)}", flush=True)
                print("threading barrier queue ok", flush=True)


            if __name__ == "__main__":
                main()
        """,
    )
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"barrier pidfile replay diverged (exit {replay.returncode})\n"
        f"record stdout:\n{_tail(record.stdout)}\n"
        f"record stderr tail:\n{_tail(record.stderr)}\n"
        f"replay stdout:\n{_tail(replay.stdout)}\n"
        f"replay stderr tail:\n{_tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
    assert "Dispatcher: too many threads waiting for item" not in combined_replay
    assert "Checkpoint difference:" not in combined_replay


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PidFile replay can overfill the dispatcher when Semaphore-released "
        "threads race through Queue.put() notification checkpoints"
    ),
)
def test_threading_semaphore_queue_pth_pidfile_replay_does_not_overfill_dispatcher(
    tmp_path: Path,
):
    record, replay = _record_and_replay_pth_script(
        tmp_path=tmp_path,
        script_name="threading_semaphore_queue_repro.py",
        script_source="""
            import queue
            import threading


            def main():
                print("=== threading_semaphore_queue_repro ===", flush=True)
                semaphore = threading.Semaphore(2)
                results = queue.Queue()

                def worker(index):
                    with semaphore:
                        results.put(index * 3)

                threads = [
                    threading.Thread(target=worker, args=(index,))
                    for index in range(6)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                values = sorted(results.get() for _ in threads)
                assert values == [0, 3, 6, 9, 12, 15]
                print(f"values={values}", flush=True)
                print("threading semaphore queue ok", flush=True)


            if __name__ == "__main__":
                main()
        """,
    )
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"semaphore pidfile replay diverged (exit {replay.returncode})\n"
        f"record stdout:\n{_tail(record.stdout)}\n"
        f"record stderr tail:\n{_tail(record.stderr)}\n"
        f"replay stdout:\n{_tail(replay.stdout)}\n"
        f"replay stderr tail:\n{_tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
    assert "Dispatcher: too many threads waiting for item" not in combined_replay
    assert "Checkpoint difference:" not in combined_replay
