"""Regression: debug-style record can crash with Werkzeug threaded serving.

This is stricter than the Flask reproducer: it removes Flask and keeps only
Werkzeug serving + threaded request handling shape that triggers the crash.

Failure signature on affected builds:
- subprocess exits with signal / trap
- request logs may appear, then process crashes during/after teardown
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_werkzeug_record_teardown_does_not_crash(tmp_path: Path):
    script = tmp_path / "werkzeug_repro.py"
    script.write_text(
        (
            "import threading, time, urllib.request\n"
            "from werkzeug.serving import make_server\n"
            "def app(environ, start_response):\n"
            "    body = b'ok'\n"
            "    start_response('200 OK', [\n"
            "        ('Content-Type', 'text/plain'),\n"
            "        ('Content-Length', str(len(body))),\n"
            "    ])\n"
            "    return [body]\n"
            "class ServerThread(threading.Thread):\n"
            "    def __init__(self):\n"
            "        super().__init__(daemon=True)\n"
            "        self.server = make_server('127.0.0.1', 0, app)\n"
            "        self.port = self.server.server_port\n"
            "    def run(self):\n"
            "        self.server.serve_forever()\n"
            "    def shutdown(self):\n"
            "        self.server.shutdown()\n"
            "t = ServerThread(); t.start(); time.sleep(0.05)\n"
            "for _ in range(20):\n"
            "    urllib.request.urlopen(f'http://127.0.0.1:{t.port}/x').read()\n"
            "t.shutdown(); t.join(timeout=5)\n"
            "print('ok')\n"
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"

    recording = tmp_path / "trace.retrace"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--quit_on_error",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    assert proc.returncode == 0, (
        f"record crashed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
