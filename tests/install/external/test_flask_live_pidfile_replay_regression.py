"""Regression for live Flask PidFile replay divergence.

The minimal trigger is a real Werkzeug server running in a background thread,
with repeated `requests` calls made during a CLI recording. Recording and
extraction succeed, but replaying the extracted PidFile can diverge when the
server thread reaches selector polling or shutdown synchronization out of
recorded order.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import textwrap

import pytest


_ROOT = Path(__file__).resolve().parents[3]


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


def test_flask_live_pidfile_replay_does_not_diverge(tmp_path: Path):
    pytest.importorskip("flask")
    pytest.importorskip("requests")
    pytest.importorskip("werkzeug")

    script = tmp_path / "flask_live_pidfile_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import socket
            import threading
            import time
            import requests
            from flask import Flask
            from werkzeug.serving import make_server


            app = Flask(__name__)


            @app.get("/x")
            def x():
                return "ok"

            @app.get("/y")
            def y():
                return "again"


            server = make_server("127.0.0.1", 0, app)
            port = server.server_port
            thread = threading.Thread(target=server.serve_forever, name="server")
            thread.start()
            try:
                deadline = time.time() + 5
                while True:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        if time.time() > deadline:
                            raise
                        time.sleep(0.05)

                first = requests.get(f"http://127.0.0.1:{port}/x", timeout=5)
                print("RESULT", first.status_code, first.text, flush=True)

                second = requests.get(f"http://127.0.0.1:{port}/y", timeout=5)
                print("RESULT", second.status_code, second.text, flush=True)
            finally:
                server.shutdown()
                thread.join(timeout=5)
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()

    recording = tmp_path / "trace.retrace"
    record = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            str(script.name),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "RESULT 200 ok" in record.stdout
    assert "RESULT 200 again" in record.stdout

    extract = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware.replay",
            "--recording",
            str(recording),
            "--extract",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert extract.returncode == 0, (
        f"extract failed (exit {extract.returncode})\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    pidfiles = sorted((tmp_path / "trace.d").glob("*.bin"))
    assert len(pidfiles) == 1

    replay = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware.replay",
            str(pidfiles[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert replay.returncode == 0, (
        f"pidfile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
