"""Regression for Flask dev-server PidFile replay after SIGINT shutdown.

The dockertest shape is a real Flask ``app.run(...)`` server recorded in the
main thread, external HTTP traffic during record, then SIGINT to stop the
recorder.  Recording and extraction succeed, and replay gets through the
recorded HTTP access logs, but broken builds see terminal replay shutdown
messages while the PidFile reader is still waiting for ordinary call results.
"""

from __future__ import annotations

from pathlib import Path
import fcntl
import os
import pty
import select
import signal
import socket
import subprocess
import sys
import termios
import textwrap
import threading
import time

import pytest


_ROOT = Path(__file__).resolve().parents[3]


def _local_pythonpath() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = [str((_ROOT / "src").resolve())]
    for rel in (
        f"build/{build_tag}/cpp/utils",
        f"build/{build_tag}/cpp/stream",
        f"build/{build_tag}/cpp/functional",
        f"build/{build_tag}/cpp/cursor",
    ):
        path = _ROOT / rel
        if path.exists():
            entries.append(str(path.resolve()))
    return os.pathsep.join(entries)


def _editable_skip() -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = []
    local_build = _ROOT / "build" / build_tag
    if local_build.exists():
        entries.append(str(local_build.resolve()))
    utils_build = _ROOT.parent / "utils" / "build" / build_tag
    if utils_build.exists():
        entries.append(str(utils_build.resolve()))
    return os.pathsep.join(entries)


def _completed_process_error(
    label: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    return (
        f"{label} failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _drain_pty(master_fd: int, chunks: list[bytes]) -> None:
    while True:
        try:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
        except OSError:
            return
        if not ready:
            continue
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            return
        if not data:
            return
        chunks.append(data)


def _claim_controlling_tty(slave_fd: int):
    def setup() -> None:
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    return setup


def test_flask_dev_server_sigint_pidfile_replay_consumes_terminal_shutdown(
    tmp_path: Path,
):
    pytest.importorskip("flask")
    requests = pytest.importorskip("requests")

    script = tmp_path / "flask_sigint_pidfile_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import time

            from flask import Flask, jsonify, request


            app = Flask(__name__)
            items = []


            @app.get("/health")
            def health():
                return jsonify(ok=True, now=time.time())


            @app.post("/items")
            def create_item():
                items.append(request.get_json(force=True))
                return jsonify(count=len(items), now=time.time()), 201


            if __name__ == "__main__":
                print("tiny flask server starting", flush=True)
                app.run(
                    host="127.0.0.1",
                    port=int(os.environ["PORT"]),
                    debug=False,
                    use_reloader=False,
                )
            """
        ),
        encoding="utf-8",
    )

    port = _free_port()
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    env["PORT"] = str(port)
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath()
    env["RETRACE_CONFIG"] = "debug"

    recording = tmp_path / "trace.retrace"
    server_log = tmp_path / "server.log"
    with server_log.open("w", encoding="utf-8") as output:
        record = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
                "--",
                script.name,
            ],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_server(requests, port)

            health = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
            assert health.status_code == 200

            created = requests.post(
                f"http://127.0.0.1:{port}/items",
                json={"name": "alpha"},
                timeout=5,
            )
            assert created.status_code == 201

            record.send_signal(signal.SIGINT)
            try:
                record_rc = record.wait(timeout=15)
            except subprocess.TimeoutExpired:
                record.kill()
                record.wait(timeout=5)
                raise
        finally:
            if record.poll() is None:
                record.kill()
                record.wait(timeout=5)

    record_output = server_log.read_text(encoding="utf-8")
    assert record_rc == 0, (
        f"record failed (exit {record_rc})\ncombined output:\n{record_output}"
    )
    assert "GET /health HTTP/1.1" in record_output
    assert "POST /items HTTP/1.1" in record_output

    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=env)
    assert extract.returncode == 0, _completed_process_error("extract", extract)

    list_pids = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=env,
    )
    assert list_pids.returncode == 0, _completed_process_error(
        "list_pids",
        list_pids,
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)

    assert replay.returncode == 0, (
        f"pidfile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "GET /health HTTP/1.1" in replay.stderr
    assert "POST /items HTTP/1.1" in replay.stderr
    assert "Unexpected message: SYNC, was expecting a result, error, or call" not in (
        replay.stdout + replay.stderr
    )
    assert "Could not read: 1 bytes from tracefile" not in (
        replay.stdout + replay.stderr
    )


def test_flask_dev_server_many_requests_sigint_pidfile_replay_replays_all_requests(
    tmp_path: Path,
):
    pytest.importorskip("flask")
    requests = pytest.importorskip("requests")

    script = tmp_path / "flask_many_requests_sigint_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import os

            from flask import Flask, jsonify


            app = Flask(__name__)
            state = {"hits": 0}


            @app.get("/")
            def index():
                state["hits"] += 1
                return jsonify(message="hello", hits=state["hits"])


            if __name__ == "__main__":
                print("many-request flask server starting", flush=True)
                app.run(
                    host="127.0.0.1",
                    port=int(os.environ["PORT"]),
                    debug=False,
                    use_reloader=False,
                )
            """
        ),
        encoding="utf-8",
    )

    port = _free_port()
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = _editable_skip()
    env["PORT"] = str(port)
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath()
    env["RETRACE_CONFIG"] = "debug"

    recording = tmp_path / "trace.retrace"
    master_fd, slave_fd = pty.openpty()
    output_chunks: list[bytes] = []
    reader = threading.Thread(
        target=_drain_pty,
        args=(master_fd, output_chunks),
        daemon=True,
    )

    record = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--",
            script.name,
        ],
        cwd=tmp_path,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        preexec_fn=_claim_controlling_tty(slave_fd),
    )
    os.close(slave_fd)
    reader.start()
    try:
        _wait_for_tcp_port(port)

        for _ in range(74):
            response = requests.get(f"http://127.0.0.1:{port}/", timeout=5)
            assert response.status_code == 200
            favicon = requests.get(
                f"http://127.0.0.1:{port}/favicon.ico",
                timeout=5,
            )
            assert favicon.status_code == 404

        os.write(master_fd, b"\x03")
        try:
            record_rc = record.wait(timeout=20)
        except subprocess.TimeoutExpired:
            record.kill()
            record.wait(timeout=5)
            raise
    finally:
        if record.poll() is None:
            record.kill()
            record.wait(timeout=5)
        try:
            os.close(master_fd)
        except OSError:
            pass

    reader.join(timeout=2)
    record_output = b"".join(output_chunks).decode("utf-8", "replace")
    assert record_rc == 0, (
        f"record failed (exit {record_rc})\ncombined output:\n{record_output}"
    )
    assert recording.exists()
    assert record_output.count('"GET / HTTP/1.1" 200') >= 74
    assert record_output.count("GET /favicon.ico HTTP/1.1") >= 74

    extract = _run([str(recording), "--extract"], cwd=tmp_path, env=env)
    assert extract.returncode == 0, _completed_process_error("extract", extract)

    list_pids = _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=env,
    )
    assert list_pids.returncode == 0, _completed_process_error(
        "list_pids",
        list_pids,
    )
    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = tmp_path / "trace.d" / f"{root_pid}.bin"
    assert pidfile.exists()

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"pidfile replay diverged before replaying all recorded requests "
        f"(exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert combined_replay.count('"GET / HTTP/1.1" 200') >= 74
    assert combined_replay.count("GET /favicon.ico HTTP/1.1") >= 74
    assert (
        "Checkpoint difference: 'SYNC' was expecting "
        "type:retracesoftware.proxy.io.CallMarkerMessage"
        not in combined_replay
    )


def _wait_for_server(requests, port: int) -> None:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(
                f"http://127.0.0.1:{port}/health",
                timeout=0.5,
            )
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - test helper reports context.
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"Flask server did not become ready: {last_error}")


def _wait_for_tcp_port(port: int) -> None:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except Exception as exc:  # noqa: BLE001 - test helper reports context.
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"Flask server did not open TCP port: {last_error}")
