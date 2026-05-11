"""Shared test helpers for retracesoftware integration tests."""
import os
from pathlib import Path
import sys
import subprocess
import textwrap

PYTHON = sys.executable
TIMEOUT = 30


def run_record(script_path, recording, extra_args=None, env=None, stacktraces=True):
    """Run a script under retrace recording.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
        "--format", "unframed_binary",
    ]
    if stacktraces:
        cmd.append("--stacktraces")
    cmd.extend(["--", str(script_path)])
    if extra_args:
        cmd.extend(extra_args)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=run_env,
    )


def run_replay(recording, extra_args=None, env=None):
    """Replay from a trace file.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
    ]
    if extra_args:
        cmd.extend(extra_args)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=run_env,
    )


def _completed_process_error(label, proc):
    return (
        f"{label} failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def tail(text, limit=12000):
    """Return the tail of a subprocess stream for compact assertion messages."""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_for_pidfile(args, *, cwd, env, timeout=TIMEOUT):
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


def local_pythonpath():
    """PYTHONPATH pointing at this checkout, preserving any caller path."""
    repo_root = Path(__file__).resolve().parents[1]
    existing = os.environ.get("PYTHONPATH")
    paths = [str(repo_root)]
    if existing:
        paths.append(existing)
    return os.pathsep.join(paths)


def record_and_replay_pth_pidfile(
    *,
    tmp_path,
    script_name,
    script_source,
    timeout=TIMEOUT,
    record_extra_env=None,
):
    """Record a temp script through the public .pth flow and replay its root PidFile."""
    script = Path(tmp_path) / script_name
    script.write_text(textwrap.dedent(script_source), encoding="utf-8")

    recording_name = "trace.retrace"
    recording = Path(tmp_path) / recording_name

    install_env = os.environ.copy()
    install_env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    install_env["PYTHONPATH"] = local_pythonpath()
    install_env.pop("RETRACE_RECORDING", None)
    install_env.pop("RETRACE_CONFIG", None)
    install_env.pop("RETRACE_SKIP_CHECKSUMS", None)

    install = _run_for_pidfile(
        [PYTHON, "-m", "retracesoftware", "install"],
        cwd=Path(tmp_path),
        env=install_env,
        timeout=timeout,
    )
    assert install.returncode == 0, _completed_process_error(
        "install auto-enable",
        install,
    )

    record_env = install_env.copy()
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_CONFIG"] = "debug"
    record_env["RETRACE_RECORDING"] = recording_name
    if record_extra_env:
        record_env.update(record_extra_env)

    record = _run_for_pidfile(
        [PYTHON, script.name],
        cwd=Path(tmp_path),
        env=record_env,
        timeout=timeout,
    )
    assert record.returncode == 0, (
        f"record failed for {script_name}\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert recording.exists()

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=Path(tmp_path),
        env=install_env,
        timeout=timeout,
    )
    assert extract.returncode == 0, _completed_process_error("extract", extract)

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=Path(tmp_path),
        env=install_env,
        timeout=timeout,
    )
    assert list_pids.returncode == 0, _completed_process_error(
        "list_pids",
        list_pids,
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = Path(tmp_path) / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = install_env.copy()
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay = _run_for_pidfile(
        [str(pidfile)],
        cwd=Path(tmp_path),
        env=replay_env,
        timeout=timeout,
    )
    return record, replay
