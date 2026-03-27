"""Regression: debug auto-enable record crashes with proxied `_io.BufferedReader`.

Root component focus:
- `install` module interception config for `_io`
- `proxy.patch_type()` descriptor wrapping on `_io.BufferedReader`
- native `utils.wrapped_member` / `WrappedFunction` recursion on Python 3.12

Observed ownership signal:
- the exact Flask/Werkzeug record crash reproduces when user module overrides
  proxy only `_io.BufferedReader`
- disabling `_io.BufferedReader` patching makes the same scenario stop crashing

This keeps the trigger close to the owning layer instead of depending on the
full default stdlib interception set.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

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


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="regression observed on Python 3.12 path",
)
def test_debug_record_flask_with_only_bufferedreader_proxy_does_not_crash(
    tmp_path: Path,
):
    script = tmp_path / "flask_repro.py"
    script.write_text(
        (
            "import json\n"
            "import socket\n"
            "import threading\n"
            "import time\n"
            "import urllib.error\n"
            "import urllib.request\n"
            "from flask import Flask, jsonify\n"
            "from werkzeug.serving import make_server\n"
            "app = Flask(__name__)\n"
            "request_count = 0\n"
            "@app.route('/health')\n"
            "def health():\n"
            "    return jsonify({'status': 'ok'})\n"
            "@app.route('/test')\n"
            "def test_endpoint():\n"
            "    global request_count\n"
            "    request_count += 1\n"
            "    return jsonify({'message': 'Hello!', 'count': request_count})\n"
            "class ServerThread(threading.Thread):\n"
            "    def __init__(self, host: str, port: int):\n"
            "        super().__init__(daemon=True)\n"
            "        self.server = make_server(host, port, app)\n"
            "        self.context = app.app_context()\n"
            "        self.context.push()\n"
            "    def run(self):\n"
            "        self.server.serve_forever()\n"
            "    def shutdown(self):\n"
            "        self.server.shutdown()\n"
            "        self.context.pop()\n"
            "def http_get_json(url: str, retries: int = 30, delay: float = 0.1) -> dict:\n"
            "    last_exc = None\n"
            "    for _ in range(retries):\n"
            "        try:\n"
            "            with urllib.request.urlopen(url, timeout=2) as resp:\n"
            "                return json.loads(resp.read().decode('utf-8'))\n"
            "        except (urllib.error.URLError, json.JSONDecodeError) as exc:\n"
            "            last_exc = exc\n"
            "            time.sleep(delay)\n"
            "    raise RuntimeError(f'Failed to GET {url}: {last_exc}')\n"
            "def free_port() -> int:\n"
            "    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:\n"
            "        s.bind(('127.0.0.1', 0))\n"
            "        return int(s.getsockname()[1])\n"
            "def main():\n"
            "    host = '127.0.0.1'\n"
            "    port = free_port()\n"
            "    base_url = f'http://{host}:{port}'\n"
            "    server = ServerThread(host, port)\n"
            "    server.start()\n"
            "    try:\n"
            "        health_resp = http_get_json(f'{base_url}/health')\n"
            "        assert health_resp.get('status') == 'ok'\n"
            "        first = http_get_json(f'{base_url}/test')\n"
            "        second = http_get_json(f'{base_url}/test')\n"
            "        assert first['message'] == 'Hello!'\n"
            "        assert first['count'] == 1\n"
            "        assert second['count'] == 2\n"
            "    finally:\n"
            "        server.shutdown()\n"
            "        server.join(timeout=5)\n"
            "        assert not server.is_alive(), 'Server thread failed to stop cleanly'\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        encoding="utf-8",
    )

    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "_io.toml").write_text(
        'proxy = ["BufferedReader"]\nimmutable = []\n',
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath()
    env["RETRACE_DEBUG"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(tmp_path / "trace.retrace")
    env["RETRACE_MODULES_PATH"] = str(modules_dir)

    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        "record crashed with only `_io.BufferedReader` proxied\n"
        f"exit: {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
