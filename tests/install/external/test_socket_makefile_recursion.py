"""Regression coverage for socket.makefile under retrace debug."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_PYTHONPATH = str(_ROOT / "src")
for _rel in (
    "build/cp312/cpp/cursor",
    "build/cp312/cpp/utils",
    "build/cp312/cpp/functional",
    "build/cp312/cpp/stream",
    ".",
):
    _PYTHONPATH += f"{os.pathsep}{_ROOT / _rel}"


def test_socket_makefile_recursion_reproducer(tmp_path):
    """Run a tiny urllib+http.server script under retrace debug config.

    The prior recursion regression is fixed; keep this as a subprocess
    smoke test so it does not silently return.
    """
    script = tmp_path / "repro_socket_makefile.py"
    script.write_text(
        textwrap.dedent(
            """
            import threading
            import urllib.request
            from http.server import BaseHTTPRequestHandler, HTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

                def log_message(self, format, *args):
                    return

            def main():
                server = HTTPServer(("127.0.0.1", 0), Handler)
                port = server.server_port
                t = threading.Thread(target=server.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                        assert resp.read() == b"ok"
                finally:
                    server.shutdown()
                    server.server_close()

            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = _PYTHONPATH
    env["RETRACE_DEBUG"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(tmp_path / "trace.bin")

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, output
    assert "RecursionError" not in output
