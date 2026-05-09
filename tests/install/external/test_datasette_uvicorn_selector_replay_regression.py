"""Regression for Datasette/Uvicorn PidFile replay shutdown routing.

The original product failure was seen by recording a Datasette server, sending
HTTP requests during record, stopping it with SIGINT, extracting the root
PidFile, and replaying that PidFile.  Replay gets through the user-visible
request path, but during asyncio/Uvicorn shutdown the reader consumes a
recorded float monotonic-clock result where selectors expects a socket fd.

Current failure signatures have moved as the replay pipeline has improved:

    Checkpoint difference: ... was expecting time.monotonic

Older builds reached asyncio shutdown and delivered a recorded time-like result
to the selector self-pipe fd call:

    ValueError: Invalid file object: 3617...

Both fingerprints belong to the same Datasette/Uvicorn PidFile replay contract:
recording succeeds, extraction succeeds, replay restarts the server, and replay
then consumes the next recorded shutdown/event-loop message in the wrong order.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest


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
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _completed_process_error(
    label: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    return (
        f"{label} failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _write_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL
            );
            INSERT INTO items (name, price, currency) VALUES
                ('Acme Water', 68.46, 'AUD'),
                ('Example Energy', 28.32, 'USD'),
                ('Castle Water', 436.55, 'GBP');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _request_json(url: str):
    with urlopen(url, timeout=5) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def _wait_for_datasette(port: int) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except Exception as exc:  # noqa: BLE001 - test helper reports context.
            last_error = exc
            time.sleep(0.2)
    raise AssertionError(f"Datasette did not become ready: {last_error}")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Datasette/Uvicorn PidFile replay misroutes shutdown/event-loop "
        "messages after SIGINT"
    ),
)
def test_datasette_uvicorn_sigint_pidfile_replay_does_not_misroute_selector_fd(
    tmp_path: Path,
):
    pytest.importorskip("datasette")

    db_path = tmp_path / "demo.db"
    _write_db(db_path)

    port = _free_port()
    recording = tmp_path / "datasette.retrace"
    record_log = tmp_path / "record.log"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    with record_log.open("w", encoding="utf-8") as output:
        record = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
                "--",
                "-m",
                "datasette",
                str(db_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--setting",
                "default_page_size",
                "5",
            ],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_datasette(port)
            rows = _request_json(
                f"http://127.0.0.1:{port}/demo/items.json?_shape=array"
            )
            assert len(rows) == 3, rows

            record.send_signal(signal.SIGINT)
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

    record_output = record_log.read_text(encoding="utf-8")
    assert record_rc in (0, -signal.SIGINT), (
        f"record failed (exit {record_rc})\ncombined output:\n{record_output}"
    )
    assert recording.exists()
    assert "GET /demo/items.json?_shape=array HTTP/1.1" in record_output

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
    pidfile = tmp_path / "datasette.d" / f"{root_pid}.bin"
    assert pidfile.exists(), pidfile

    replay_env = env.copy()
    replay_env["RETRACE_SKIP_CHECKSUMS"] = "1"
    replay = _run([str(pidfile)], cwd=tmp_path, env=replay_env)
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"pidfile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "Checkpoint difference" not in combined_replay
    assert "Invalid file object" not in combined_replay
    assert "GET /demo/items.json?_shape=array HTTP/1.1" in combined_replay
