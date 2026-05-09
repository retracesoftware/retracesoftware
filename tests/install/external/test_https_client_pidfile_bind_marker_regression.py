"""Regressions for HTTPS client PidFile replay bind-marker divergence.

The requests and coreapi dockertests both record successfully, extract a
single PidFile, and previously failed on replay with:

    RuntimeError: bind marker returned when bind was expected

The common owning stack is requests/urllib3 over TLS.  A plain HTTP request does
not reproduce this on current main; the HTTPS close/cleanup path does.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import textwrap

import pytest


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


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    return env


def test_requests_https_pidfile_replay_does_not_consume_bind_marker(
    tmp_path: Path,
):
    pytest.importorskip("requests")

    client = tmp_path / "requests_https_client.py"
    client.write_text(
        textwrap.dedent(
            """
            import os

            import requests
            import urllib3


            urllib3.disable_warnings()

            with requests.Session() as session:
                response = session.get(os.environ["URL"], timeout=5, verify=False)
                response.raise_for_status()
                print(response.text, flush=True)
            """
        ),
        encoding="utf-8",
    )

    server = _start_one_request_https_server(tmp_path, b"ok-local-https")
    try:
        record_env = _base_env()
        record_env["URL"] = f"https://127.0.0.1:{server.port}/"
        recording = tmp_path / "test.retrace"

        record = _record(client, recording, env=record_env)
        assert record.returncode == 0, _completed_process_error("record", record)
        assert record.stdout == "ok-local-https\n"
    finally:
        server.wait()

    replay = _extract_and_replay(recording, tmp_path, _base_env())
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "requests HTTPS PidFile replay diverged\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
    assert "bind marker returned when bind was expected" not in combined


def test_coreapi_https_pidfile_replay_does_not_consume_bind_marker(
    tmp_path: Path,
):
    pytest.importorskip("coreapi")

    client = tmp_path / "coreapi_https_client.py"
    client.write_text(
        textwrap.dedent(
            """
            import coreapi


            response = coreapi.Client().get("https://api.github.com")
            print(response["current_user_url"], flush=True)
            """
        ),
        encoding="utf-8",
    )

    env = _base_env()
    recording = tmp_path / "test.retrace"

    record = _record(client, recording, env=env)
    if record.returncode != 0:
        pytest.skip(
            "coreapi public HTTPS record did not complete; likely network or "
            f"GitHub availability issue\nstdout:\n{record.stdout}\nstderr:\n{record.stderr}"
        )
    assert "https://api.github.com/user" in record.stdout

    replay = _extract_and_replay(recording, tmp_path, env)
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "coreapi HTTPS PidFile replay diverged\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
    assert "bind marker returned when bind was expected" not in combined


def _record(
    script: Path,
    recording: Path,
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--",
            script.name,
        ],
        cwd=script.parent,
        env=env,
    )


def _extract_and_replay(
    recording: Path,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    extract = _run([str(recording), "--extract"], cwd=cwd, env=env)
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
        cwd=cwd,
        env=env,
    )
    assert list_pids.returncode == 0, _completed_process_error(
        "list_pids",
        list_pids,
    )

    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = cwd / "test.d" / f"{root_pid}.bin"
    assert pidfile.exists(), pidfile

    replay_env = env.copy()
    replay_env["URL"] = "https://127.0.0.1:9/"
    return _run([str(pidfile)], cwd=cwd, env=replay_env)


class _ServerProcess:
    def __init__(self, process: subprocess.Popen[str], port: int):
        self.process = process
        self.port = port

    def wait(self) -> None:
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise
        assert self.process.returncode == 0, (
            "HTTPS fixture server failed\n"
            f"stdout:\n{self.process.stdout.read() if self.process.stdout else ''}\n"
            f"stderr:\n{self.process.stderr.read() if self.process.stderr else ''}"
        )


def _start_one_request_https_server(tmp_path: Path, body: bytes) -> _ServerProcess:
    openssl = _run(
        ["openssl", "version"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
    )
    if openssl.returncode != 0:
        pytest.skip("openssl is required to generate a local HTTPS fixture cert")

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    openssl_config = tmp_path / "openssl.cnf"
    openssl_config.write_text(
        textwrap.dedent(
            """
            [req]
            distinguished_name=req_distinguished_name
            x509_extensions=v3_req
            prompt=no
            [req_distinguished_name]
            CN=127.0.0.1
            [v3_req]
            subjectAltName=@alt_names
            [alt_names]
            IP.1=127.0.0.1
            DNS.1=localhost
            """
        ),
        encoding="utf-8",
    )
    create_cert = _run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-config",
            str(openssl_config),
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=30,
    )
    assert create_cert.returncode == 0, _completed_process_error(
        "openssl cert generation",
        create_cert,
    )

    server = tmp_path / "https_server.py"
    server.write_text(
        textwrap.dedent(
            f"""
            from http.server import BaseHTTPRequestHandler, HTTPServer
            import os
            import ssl


            BODY = {body!r}


            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY)

                def log_message(self, *args):
                    pass


            httpd = HTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(os.environ["CERT"], os.environ["KEY"])
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
            print(httpd.server_address[1], flush=True)
            httpd.handle_request()
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CERT"] = str(cert)
    env["KEY"] = str(key)
    process = subprocess.Popen(
        [sys.executable, str(server)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    port_line = process.stdout.readline().strip()
    assert port_line, (
        "HTTPS fixture server did not print a port\n"
        f"stderr:\n{process.stderr.read() if process.stderr else ''}"
    )
    return _ServerProcess(process, int(port_line))
