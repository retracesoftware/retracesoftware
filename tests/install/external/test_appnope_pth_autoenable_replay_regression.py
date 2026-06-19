"""Regression: appnope + Retrace venv replay bootstrap diverges.

The public recording flow is:

    python -m retracesoftware venv .venv
    RETRACE_RECORDING=trace.retrace .venv/bin/python app.py

On macOS, an app that imports ``appnope`` and starts a
``multiprocessing.Process`` must record and replay through that venv path without
enabling retrace inside multiprocessing helper bootstrap processes or
desynchronizing debug checkpoints around the spawn pipe writes.

The same app body passes through the direct wrapper flow:

    python -m retracesoftware --recording trace.retrace -- app.py
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import textwrap

import pytest


_ROOT = Path(__file__).resolve().parents[3]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="appnope is a macOS app-nap integration library",
)
def test_appnope_retrace_venv_pidfile_replay_patches_loaded_io_open():
    pytest.importorskip("appnope")

    workdir = Path(tempfile.mkdtemp(prefix="retrace-appnope-venv-", dir="/tmp"))
    script = workdir / "appnope_retrace_venv_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            from multiprocessing import Process

            import appnope


            def child_task():
                return None


            if __name__ == "__main__":
                print("=== appnope_retrace_venv ===", flush=True)
                if sys.platform == "darwin":
                    appnope.nope()
                    print("disabled app nap", flush=True)
                else:
                    print(f"non-macOS platform ({sys.platform}); appnope no-op", flush=True)

                proc = Process(target=child_task)
                proc.start()
                print("process started", flush=True)
                proc.terminate()
                proc.join(timeout=5)
                print("terminated", flush=True)
            """
        ),
        encoding="utf-8",
    )

    recording_name = "trace.retrace"

    python = shlex.quote(sys.executable)
    venv_dir = ".retrace-venv"
    retrace_python = shlex.quote(f"{venv_dir}/bin/python")
    command = textwrap.dedent(
        f"""
        set -e
        cd {shlex.quote(str(workdir))}
        {python} -m retracesoftware venv {venv_dir} --without-pip --system-site-packages
        rm -f {recording_name}
        rm -rf trace.d
        RETRACE_RECORDING={recording_name} RETRACE_CONFIG=debug {retrace_python} {shlex.quote(script.name)} > record.log 2>&1
        ./{recording_name} --extract > extract.log 2>&1
        ROOT_PID=$({python} -m retracesoftware --recording {recording_name} --list_pids | head -1)
        ./trace.d/${{ROOT_PID}}.bin > replay.log 2>&1
        """
    )

    result = _run(["zsh", "-lc", command], cwd=_ROOT, env=os.environ.copy())
    replay_log = (workdir / "replay.log").read_text(
        encoding="utf-8",
        errors="replace",
    ) if (workdir / "replay.log").exists() else ""
    combined = result.stdout + result.stderr + replay_log

    assert result.returncode == 0, (
        "pth appnope manual record/extract/replay flow diverged "
        f"(exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
        f"replay.log:\n{replay_log}"
    )
    assert "failed to patch _io.open" not in combined
    assert "Could not read: 1 bytes from tracefile" not in combined
    assert "=== appnope_retrace_venv ===" in combined
    assert "process started" in combined
    assert "terminated" in combined
