"""Standalone WSGI replay smoke test.

Keep this test subprocess-based so it can run on its own without the
install-suite fixture stack, and so collection does not depend on the
editable loader state of local native builds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def _local_pythonpath() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
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


def _editable_skip() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = [str(_ROOT / "build" / build_tag)]
    utils_root = _ROOT.parent / "utils" / "build" / build_tag
    if utils_root.exists():
        entries.append(str(utils_root))
    return os.pathsep.join(entries)


def _run_retrace_script(tmp_path, source: str):
    script = tmp_path / "henry2_repro.py"
    script.write_text(textwrap.dedent(source), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = _local_pythonpath()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    env["RETRACE_DEBUG"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    return proc, (proc.stdout or "") + (proc.stderr or "")


def test_wsgiref_two_requests_replay_diverges_cleanly(tmp_path):
    proc, output = _run_retrace_script(
        tmp_path,
        """
        import signal
        import socket
        import threading
        from wsgiref.simple_server import make_server

        from retracesoftware.install import ReplayDivergence
        from tests.runner import Runner


        runner = Runner()
        responses = []
        state = {}


        def _http_get(port: int, path: str) -> bytes:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(f"GET {path} HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n".encode())
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            s.close()
            return b"".join(chunks)


        def app(environ, start_response):
            body = b"Hello, World!"
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/plain"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]


        def server_work():
            srv = make_server("127.0.0.1", 0, app)
            state["srv"] = srv
            port = srv.server_address[1]

            def client(path: str):
                responses.append(_http_get(port, path))

            t1 = threading.Thread(target=client, args=("/one",))
            t1.start()
            srv._handle_request_noblock()
            t1.join(timeout=5)
            assert not t1.is_alive()

            t2 = threading.Thread(target=client, args=("/two",))
            t2.start()
            srv._handle_request_noblock()
            t2.join(timeout=5)
            assert not t2.is_alive()


        recording = runner.record(server_work)
        state.pop("srv").server_close()
        assert recording.error is None, recording.error
        assert len(responses) == 2
        assert all(b"Hello, World!" in response for response in responses), responses


        def _timeout_handler(signum, frame):
            raise TimeoutError("replay timed out")


        responses.clear()
        state.clear()
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(15)
        try:
            try:
                runner.replay(recording, server_work)
            except ReplayDivergence:
                print("ReplayDivergence")
            else:
                print("ReplaySucceeded")
        finally:
            signal.alarm(0)
            if "srv" in state:
                state["srv"].server_close()
        """,
    )

    assert proc.returncode == 0, output
    assert "ReplayDivergence" in output, output
    assert "timed out" not in output.lower(), output
