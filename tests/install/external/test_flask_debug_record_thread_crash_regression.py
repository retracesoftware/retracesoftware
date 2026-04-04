"""Regression: Flask debug-style record can crash during teardown.

Root component focus:
- `retracesoftware.stream` background writer/heartbeat thread lifecycle during
  record shutdown under `--stacktraces --quit_on_error`.
- Crash manifests after request handling completes, with native stop in
  Python type lookup from a non-main thread (`EXC_BAD_ACCESS` / signal exit).

This keeps the trigger minimal while preserving the observed concurrency shape
(Flask + Werkzeug server thread + two requests).
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_flask_record_teardown_does_not_crash_background_writer(tmp_path: Path):
    script = tmp_path / "flask_repro.py"
    script.write_text(
        (
            "import threading, time, urllib.request\n"
            "from flask import Flask\n"
            "from werkzeug.serving import make_server\n"
            "app = Flask(__name__)\n"
            "@app.route('/x')\n"
            "def x():\n"
            "    return 'x'\n"
            "class ServerThread(threading.Thread):\n"
            "    def __init__(self):\n"
            "        super().__init__(daemon=True)\n"
            "        self.server = make_server('127.0.0.1', 0, app)\n"
            "        self.port = self.server.server_port\n"
            "        self.ctx = app.app_context(); self.ctx.push()\n"
            "    def run(self):\n"
            "        self.server.serve_forever()\n"
            "    def shutdown(self):\n"
            "        self.server.shutdown(); self.ctx.pop()\n"
            "t = ServerThread(); t.start(); time.sleep(0.05)\n"
            "urllib.request.urlopen(f'http://127.0.0.1:{t.port}/x').read()\n"
            "urllib.request.urlopen(f'http://127.0.0.1:{t.port}/x').read()\n"
            "t.shutdown(); t.join(timeout=5)\n"
            "print('ok')\n"
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"

    # Repeat a few times because this teardown crash is timing-sensitive.
    for i in range(6):
        recording = tmp_path / f"trace_{i}.retrace"
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
            f"record crashed on iteration {i + 1} (exit {proc.returncode})\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
