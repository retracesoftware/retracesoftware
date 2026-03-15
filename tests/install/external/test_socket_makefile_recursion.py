"""Regression coverage for socket.makefile under retrace debug."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]


def _local_pythonpath() -> str:
    build_tag = f"cp{sys.version_info.major}{sys.version_info.minor}{getattr(sys, 'abiflags', '')}"
    entries = [str(_ROOT / "src")]
    for rel in (
        f"build/{build_tag}/cpp/cursor",
        f"build/{build_tag}/cpp/utils",
        f"build/{build_tag}/cpp/functional",
        f"build/{build_tag}/cpp/stream",
    ):
        path = _ROOT / rel
        if path.exists():
            entries.append(str(path))
    entries.append(str(_ROOT))
    return os.pathsep.join(entries)


def _run_retrace_script(tmp_path, source: str):
    script = tmp_path / "repro_socket_makefile.py"
    script.write_text(textwrap.dedent(source), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = _local_pythonpath()
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

    return proc, (proc.stdout or "") + (proc.stderr or "")


def test_socket_makefile_recursion_reproducer(tmp_path):
    """Run a tiny urllib+http.server script under retrace debug config.

    The prior recursion regression is fixed; keep this as a subprocess
    smoke test so it does not silently return.
    """
    proc, output = _run_retrace_script(
        tmp_path,
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
        """,
    )

    assert proc.returncode == 0, output
    assert "RecursionError" not in output


def test_socketio_readable_reproducer(tmp_path):
    proc, output = _run_retrace_script(
        tmp_path,
        """
        import socket

        left, right = socket.socketpair()
        try:
            raw = socket.SocketIO(left, "rb")
            assert raw.readable() is True
        finally:
            left.close()
            right.close()
        """,
    )

    assert proc.returncode == 0, output


def test_bufferedreader_socketio_reproducer(tmp_path):
    proc, output = _run_retrace_script(
        tmp_path,
        """
        import io
        import socket

        left, right = socket.socketpair()
        try:
            raw = socket.SocketIO(left, "rb")
            buffered = io.BufferedReader(raw, 8192)
            assert isinstance(buffered, io.BufferedReader)
        finally:
            left.close()
            right.close()
        """,
    )

    assert proc.returncode == 0, output
