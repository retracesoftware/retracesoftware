"""Regression: threaded wsgiref replay can deadlock in replay scheduling.

This is the deterministic version of the flaky top-level Henry WSGI smoke:
recording succeeds, but replay can leave the WSGI request thread and the
client thread both waiting in proxy.io for their next recorded external result.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.helpers import PYTHON

_ROOT = Path(__file__).resolve().parents[3]


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


@pytest.mark.xfail(
    reason=(
        "threaded wsgiref replay can deadlock with both replay threads waiting "
        "for proxy.io scheduler results"
    ),
)
def test_wsgiref_many_threaded_requests_replay_does_not_deadlock(tmp_path: Path):
    script = tmp_path / "wsgiref_threaded_replay_deadlock.py"
    script.write_text(
        textwrap.dedent(
            """
            import socket
            import threading
            from wsgiref.simple_server import make_server

            from retracesoftware.install import ReplayDivergence
            from tests.runner import Runner


            runner = Runner()
            responses = []
            state = {}


            def _http_get(port: int, path: str, ready=None) -> bytes:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", port))
                request = (
                    f"GET {path} HTTP/1.0\\r\\n"
                    "Host: 127.0.0.1\\r\\n\\r\\n"
                ).encode()
                sock.sendall(request)
                if ready is not None:
                    ready.set()

                chunks = []
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                sock.close()
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

                def client(path: str, ready):
                    responses.append(_http_get(port, path, ready))

                for index in range(100):
                    ready = threading.Event()
                    thread = threading.Thread(
                        target=client,
                        args=(f"/req{index}", ready),
                    )
                    thread.start()
                    assert ready.wait(timeout=5)
                    srv._handle_request_noblock()
                    thread.join(timeout=5)
                    assert not thread.is_alive()


            recording = runner.record(server_work)
            state.pop("srv").server_close()
            assert recording.error is None, recording.error
            assert len(responses) == 100
            assert all(b"Hello, World!" in response for response in responses)

            responses.clear()
            state.clear()
            try:
                try:
                    runner.replay(recording, server_work)
                except ReplayDivergence:
                    print("ReplayDivergence", flush=True)
                else:
                    print("ReplaySucceeded", flush=True)
            finally:
                if "srv" in state:
                    state["srv"].server_close()
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = _local_pythonpath()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    env["RETRACE_DEBUG"] = "1"
    env.pop("RETRACE_CONFIG", None)
    env.pop("RETRACE_RECORDING", None)

    for attempt in range(1, 3):
        try:
            proc = subprocess.run(
                [PYTHON, str(script)],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            pytest.fail(
                f"attempt {attempt} timed out during threaded wsgiref replay\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, output
        assert "ReplaySucceeded" in output, output
