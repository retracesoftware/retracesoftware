from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_record_then_replay_flask_request_from_unretraced_client_thread(
    tmp_path: Path,
):
    pytest.importorskip("flask")

    script_file = tmp_path / "flask_unretraced_client.py"
    script_file.write_text(
        (
            "import os\n"
            "import socket\n"
            "import threading\n"
            "from flask import Flask\n"
            "from wsgiref.simple_server import make_server\n"
            "\n"
            "def http_get(port, path):\n"
            "    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "    try:\n"
            "        client.connect(('127.0.0.1', port))\n"
            "        client.sendall(\n"
            "            f'GET {path} HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n'.encode('ascii')\n"
            "        )\n"
            "        chunks = []\n"
            "        while True:\n"
            "            chunk = client.recv(4096)\n"
            "            if not chunk:\n"
            "                break\n"
            "            chunks.append(chunk)\n"
            "        return b''.join(chunks)\n"
            "    finally:\n"
            "        client.close()\n"
            "\n"
            "app = Flask(__name__)\n"
            "\n"
            "@app.route('/hello')\n"
            "def hello():\n"
            "    return 'Hello from Flask!'\n"
            "\n"
            "server = make_server('127.0.0.1', 0, app)\n"
            "server.socket.settimeout(2.0)\n"
            "port = server.server_address[1]\n"
            "\n"
            "def client():\n"
            "    http_get(port, '/hello')\n"
            "\n"
            "thread = None\n"
            "if os.environ.get('RETRACE_LIVE_CLIENT') == '1':\n"
            "    thread = threading.Thread(target=client)\n"
            "    thread.start()\n"
            "\n"
            "server._handle_request_noblock()\n"
            "server.server_close()\n"
            "\n"
            "if thread is not None:\n"
            "    thread.join(timeout=5)\n"
            "    assert not thread.is_alive(), 'client thread hung'\n"
            "\n"
            "print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    trace_file = str(tmp_path / "flask_unretraced_client.retrace")

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    record_env = env | {"RETRACE_LIVE_CLIENT": "1"}
    replay_env = env.copy()

    record = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            trace_file,
            "--format",
            "unframed_binary",
            "--",
            str(script_file),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=record_env,
    )
    assert record.returncode == 0, (
        "record failed for Flask unretraced-client roundtrip\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            trace_file,
            "--format",
            "unframed_binary",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=replay_env,
    )
    assert replay.returncode == 0, (
        "replay diverged for Flask unretraced-client roundtrip\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
