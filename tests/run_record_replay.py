"""Reusable record/replay helpers for end-to-end tests.

Each helper records a script, then replays from the trace,
asserting exit codes and stdout match.
"""
import os
import sys
import subprocess
import threading

PYTHON = sys.executable
TIMEOUT = 30


def record_then_replay(tmpdir, script_file):
    """Record to disk, then replay from disk.

    Creates a ``recording/`` subdirectory inside *tmpdir*.
    """
    recording_dir = os.path.join(tmpdir, "recording")

    # --- record ---
    rec = subprocess.run(
        [PYTHON, "-m", "retracesoftware",
         "--recording", recording_dir,
         "--", script_file],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    print("RECORD stdout:", repr(rec.stdout))
    print("RECORD stderr:", rec.stderr)

    assert rec.returncode == 0, f"Record failed (exit {rec.returncode}):\n{rec.stderr}"
    assert os.path.isfile(os.path.join(recording_dir, "trace.bin")), \
        "trace.bin not created"

    _replay_and_compare(recording_dir, rec)


def record_then_replay_via_pipe(pipedir, script_file):
    """Record through a FIFO, materialise the trace, then replay.

    *pipedir* must already contain a FIFO at ``trace.bin``
    (e.g. created by ``os.mkfifo``).
    """
    pipe_path = os.path.join(pipedir, "trace.bin")

    drain_result = {}
    reader = threading.Thread(
        target=_drain_pipe, args=(pipe_path, drain_result))
    reader.start()

    rec = subprocess.run(
        [PYTHON, "-m", "retracesoftware",
         "--recording", pipedir,
         "--create_tracedir_cmd", "true",
         "--", script_file],
        capture_output=True, text=True, timeout=TIMEOUT,
    )

    reader.join(timeout=TIMEOUT)

    print("RECORD stdout:", repr(rec.stdout))
    print("RECORD stderr:", rec.stderr)

    assert rec.returncode == 0, f"Record failed (exit {rec.returncode}):\n{rec.stderr}"
    assert not reader.is_alive(), "Drain thread did not finish"

    trace_bytes = drain_result.get("data", b"")
    assert len(trace_bytes) > 0, "No trace bytes read from pipe"

    os.unlink(pipe_path)
    with open(pipe_path, "wb") as f:
        f.write(trace_bytes)

    _replay_and_compare(pipedir, rec)


# -- internal helpers --

def _drain_pipe(path, result):
    with open(path, "rb") as f:
        result["data"] = f.read()


def _replay_and_compare(recording_dir, rec):
    rep = subprocess.run(
        [PYTHON, "-m", "retracesoftware",
         "--recording", recording_dir],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    print("REPLAY stdout:", repr(rep.stdout))
    print("REPLAY stderr:", rep.stderr)

    assert rep.returncode == 0, f"Replay failed (exit {rep.returncode}):\n{rep.stderr}"
    assert rec.stdout == rep.stdout, (
        f"stdout mismatch:\n  record: {rec.stdout!r}\n  replay: {rep.stdout!r}"
    )
