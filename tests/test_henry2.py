"""Standalone WSGI replay smoke test.

Keep this test subprocess-based so it can run on its own without the
install-suite fixture stack, and so collection does not depend on the
editable loader state of local native builds.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.helpers import PYTHON


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
    # This script records/replays through tests.runner.Runner. Do not also
    # auto-enable the whole subprocess: nested Systems cannot both own the same
    # process-global type patches.
    env.pop("RETRACE_CONFIG", None)
    env.pop("RETRACE_RECORDING", None)

    proc = subprocess.run(
        [PYTHON, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )

    return proc, (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.skipif(
    os.environ.get("RETRACE_TEST_INSTALLED_WHEEL") == "1"
    and sys.version_info[:2] == (3, 12)
    and platform.machine().lower() in {"aarch64", "arm64"},
    reason="ARM Python 3.12 installed-wheel CI can hang this heavyweight WSGI replay smoke",
)
def test_wsgiref_two_requests_replay_succeeds_without_timeout(tmp_path):
    proc, output = _run_retrace_script(
        tmp_path,
        """
        import signal
        import socket
        import threading
        import traceback
        from wsgiref.simple_server import make_server

        from retracesoftware.install import ReplayDivergence
        from tests.runner import Runner


        runner = Runner()
        responses = []
        state = {}


        def _http_get(port: int, path: str, ready=None) -> bytes:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(f"GET {path} HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n".encode())
            if ready is not None:
                ready.set()
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


        unretraced_http_get = runner.unretraced(_http_get)
        unretraced_call = runner.unretraced(lambda function, *args: function(*args))


        def start_client(port: int, path: str):
            def launch():
                ready = threading.Event()

                def client():
                    responses.append(unretraced_http_get(port, path, ready))

                thread = threading.Thread(target=client)
                thread.start()
                assert ready.wait(timeout=5)
                return thread

            return unretraced_call(launch)


        def join_client(thread):
            def join():
                thread.join(timeout=5)
                assert not thread.is_alive()

            unretraced_call(join)


        def server_work(client_starter):
            srv = make_server("127.0.0.1", 0, app)
            state["srv"] = srv
            port = srv.server_address[1]

            for path in ("/one", "/two"):
                thread = None
                if client_starter is not None:
                    thread = client_starter(port, path)
                srv._handle_request_noblock()
                if thread is not None:
                    join_client(thread)


        recording = runner.record(server_work, start_client)
        state.pop("srv").server_close()
        assert recording.error is None, recording.error
        assert len(responses) == 2
        assert all(b"Hello, World!" in response for response in responses), responses


        def _timeout_handler(signum, frame):
            raise TimeoutError("replay timed out")


        responses.clear()
        state.clear()
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(30)
        try:
            try:
                runner.replay(recording, server_work, None)
            except ReplayDivergence as exc:
                print(f"ReplayDivergence: {exc}")
                if exc.__cause__ is not None:
                    print(f"ReplayDivergence cause: {type(exc.__cause__).__name__}: {exc.__cause__}")
                    traceback.print_exception(exc.__cause__)
                for note in getattr(exc, "__notes__", ()):
                    print(f"ReplayDivergence note: {note}")
            else:
                print("ReplaySucceeded")
        finally:
            signal.alarm(0)
            if "srv" in state:
                state["srv"].server_close()
        """,
    )

    assert proc.returncode == 0, output
    assert "ReplaySucceeded" in output, output
    assert "timed out" not in output.lower(), output
