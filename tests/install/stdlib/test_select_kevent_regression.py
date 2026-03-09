"""Regression: debug auto-enable fails when selectors yields kevent.

This is a minimal stdlib-only reproducer for the Flask/Werkzeug failure:
- selectors.DefaultSelector.select() reaches kqueue.control() on macOS
- Retrace record path receives a raw select.kevent object
- stream serialization falls back to pickle and crashes in debug mode
"""

import os
from pathlib import Path
import select
import subprocess
import sys

import pytest


needs_kqueue = pytest.mark.skipif(
    not hasattr(select, "kqueue"), reason="kqueue not available")


@needs_kqueue
def test_debug_record_selectors_kevent_does_not_crash(tmp_path):
    script = tmp_path / "selectors_kevent_repro.py"
    script.write_text(
        """
import selectors
import socket
import threading

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
srv.listen(1)

def client():
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect(("127.0.0.1", port))
    c.sendall(b"ping")
    c.close()

t = threading.Thread(target=client)
t.start()
conn, _ = srv.accept()

sel = selectors.DefaultSelector()
sel.register(conn, selectors.EVENT_READ)
ready = sel.select(timeout=1.0)
assert ready, ready
conn.recv(4)

sel.unregister(conn)
sel.close()
conn.close()
srv.close()
t.join()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    recording = str(tmp_path / "trace.retrace")
    env = os.environ.copy()
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = recording

    if "RETRACE_REPLAY_BIN" not in env:
        from retracesoftware.replay import extract_binary_path
        env["RETRACE_REPLAY_BIN"] = extract_binary_path()

    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        f"Record failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "cannot pickle 'select.kevent' object" not in proc.stderr
