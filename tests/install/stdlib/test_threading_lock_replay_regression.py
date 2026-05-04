"""Regression: bare ``threading.Lock()`` should replay cleanly.

Root component focus:
- CLI/raw-tape replay through ``src/retracesoftware/proxy/io.py``
- callback/binding message ordering for patched stdlib lock allocation

Ownership signal:
- this is the smallest confirmed reproducer beneath ``threading.Event()``
  and the AnyIO blocking-portal failure
- if this fails, higher-level thread/portal replay regressions may just be
  downstream symptoms of the same raw replay bug
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.helpers import PYTHON, TIMEOUT, run_record, run_replay


def test_replay_threading_lock_does_not_diverge(tmp_path: Path):
    script = tmp_path / "threading_lock_repro.py"
    script.write_text(
        (
            "import threading\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print('=== threading_lock ===', flush=True)\n"
            "    lock = threading.Lock()\n"
            "    print(f'lock_type={type(lock).__name__}', flush=True)\n"
            "    print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for threading.Lock reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for threading.Lock reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def test_replay_package_module_queue_lock_does_not_diverge(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "audit.py").write_text(
        (
            "import queue\n"
            "\n"
            "def build():\n"
            "    work_queue = queue.Queue()\n"
            "    result_queue = queue.Queue()\n"
            "    work_queue.put('work')\n"
            "    result_queue.put(work_queue.get())\n"
            "    print(result_queue.get(), flush=True)\n"
        ),
        encoding="utf-8",
    )
    (package / "main.py").write_text(
        (
            "from app.audit import build\n"
            "\n"
            "def main():\n"
            "    build()\n"
            "    print('ok', flush=True)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = subprocess.run(
        [
            PYTHON, "-m", "retracesoftware",
            "--recording", str(recording),
            "--format", "unframed_binary",
            "--", "-m", "app.main",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )
    assert record.returncode == 0, (
        "record failed for package queue.Lock reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert (package / "__pycache__").is_dir()

    replay = subprocess.run(
        [PYTHON, "-m", "retracesoftware", "--recording", str(recording)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )
    assert replay.returncode == 0, (
        "replay diverged for package queue.Lock reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def test_replay_threading_active_limbo_lock_does_not_diverge(tmp_path: Path):
    script = tmp_path / "threading_active_limbo_lock_repro.py"
    script.write_text(
        (
            "import threading\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print('=== threading_active_limbo_lock ===', flush=True)\n"
            "    with threading._active_limbo_lock:\n"
            "        print('inside', flush=True)\n"
            "    print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for threading._active_limbo_lock reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for threading._active_limbo_lock reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def test_replay_logging_module_lock_does_not_diverge(tmp_path: Path):
    script = tmp_path / "logging_lock_repro.py"
    script.write_text(
        (
            "import logging\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print('=== logging_lock ===', flush=True)\n"
            "    with logging._lock:\n"
            "        print('inside', flush=True)\n"
            "    print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for logging._lock reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for logging._lock reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
