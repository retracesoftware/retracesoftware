"""External reproducer for socket.makefile recursion under retrace debug."""

import os
import subprocess
import sys
import textwrap


def test_socket_makefile_recursion_reproducer(tmp_path):
    """Run a tiny urllib+http.server script under retrace debug config.

    This reproduces the current RecursionError seen in the socket.makefile ->
    io.BufferedReader path when retrace is auto-enabled via RETRACE_CONFIG.
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
    assert proc.returncode != 0
    assert "RecursionError" in output
