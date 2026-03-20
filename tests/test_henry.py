import os
import subprocess
import sys
import textwrap

import pytest
from retracesoftware.install import stream_writer


pytest.importorskip("requests")


def test_stream_writer_write_call_serializes_kwargs_as_payloads():
    class RecordingWriter:
        def __init__(self):
            self.type_serializer = {}
            self.calls = []

        def handle(self, name):
            def emit(*args):
                self.calls.append((name, args))
            return emit

        def bind(self, obj):
            self.calls.append(("bind", (obj,)))

        def intern(self, obj):
            self.calls.append(("intern", (obj,)))

    raw_writer = RecordingWriter()
    writer = stream_writer(raw_writer)

    writer.write_call("socket", fileno=123, family=2)

    assert raw_writer.calls == [
        ("CALL", (("socket",), {"fileno": 123, "family": 2})),
    ]


def test_requests_get_record_only_exits_without_cleanup_traceback(tmp_path):
    script = tmp_path / "requests_record_only.py"
    script.write_text(
        textwrap.dedent(
            """
            import threading
            from http.server import BaseHTTPRequestHandler, HTTPServer

            import requests


            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"Hello from test server"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    return


            server = HTTPServer(("127.0.0.1", 0), Handler)
            port = server.server_address[1]
            t = threading.Thread(target=server.handle_request)
            t.start()
            resp = requests.get(f"http://127.0.0.1:{port}/hello", timeout=5)
            print(resp.text)
            t.join()
            server.server_close()
            """
        ),
        encoding="utf-8",
    )

    trace_path = tmp_path / "trace.retrace"
    proc = subprocess.run(
        [sys.executable, "-m", "retracesoftware", "--recording", str(trace_path), "--", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
    )

    assert proc.returncode == 0
    assert trace_path.is_file()
    assert proc.stdout == "Hello from test server\n"
    assert "Traceback (most recent call last):" not in proc.stderr
    assert (
        "TypeError: descriptor '_enter_' for '_thread.lock' objects "
        "doesn't apply to a 'lock' object"
    ) not in proc.stderr