from __future__ import annotations

import os
import socket
import sys
import time
from urllib.parse import urlparse

import requests


BASE_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:5000")
REQUEST_PAIRS = int(os.environ.get("FLASK_BASIC_1000_REQUEST_PAIRS", "1000"))
REQUEST_TIMEOUT = float(os.environ.get("FLASK_BASIC_1000_REQUEST_TIMEOUT", "120"))


def wait_for_server() -> None:
    parsed = urlparse(BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                print("Flask 1000-request server is ready", flush=True)
                return
        except Exception as exc:  # noqa: BLE001 - load generator reports context.
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"Flask server did not become ready: {last_error}")


def run_load() -> None:
    print("=== flask_basic_1000_requests_test client ===", flush=True)
    wait_for_server()

    session = requests.Session()
    for index in range(REQUEST_PAIRS):
        response = session.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert response.status_code == 200, response.status_code

        favicon = session.get(f"{BASE_URL}/favicon.ico", timeout=REQUEST_TIMEOUT)
        assert favicon.status_code == 404, favicon.status_code

        if (index + 1) % 100 == 0:
            print(f"sent {index + 1} request pairs", flush=True)

    print(f"completed {REQUEST_PAIRS} request pairs", flush=True)


if __name__ == "__main__":
    try:
        run_load()
    except Exception as exc:  # noqa: BLE001 - top-level test client failure.
        print(f"client failed: {exc}", file=sys.stderr, flush=True)
        raise
