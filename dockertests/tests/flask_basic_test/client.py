"""Load generator for flask_basic_test.

This mirrors the browser-refresh repro: many requests to "/" with the matching
browser-style "/favicon.ico" miss after each page request.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from urllib.parse import urlparse

import requests


BASE_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:5000")
REQUEST_PAIRS = int(os.environ.get("FLASK_BASIC_REQUEST_PAIRS", "74"))


def wait_for_server() -> None:
    parsed = urlparse(BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                print("Flask basic server is ready", flush=True)
                return
        except Exception as exc:  # noqa: BLE001 - load generator reports context.
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Flask basic server did not become ready: {last_error}")


def run_load() -> None:
    print("=== flask_basic_test client ===", flush=True)
    wait_for_server()

    for index in range(REQUEST_PAIRS):
        response = requests.get(f"{BASE_URL}/", timeout=5)
        assert response.status_code == 200, response.status_code
        favicon = requests.get(f"{BASE_URL}/favicon.ico", timeout=5)
        assert favicon.status_code == 404, favicon.status_code
        if (index + 1) % 10 == 0:
            print(f"sent {index + 1} request pairs", flush=True)

    print(
        f"Flask basic load generation complete: {REQUEST_PAIRS} request pairs",
        flush=True,
    )


if __name__ == "__main__":
    try:
        run_load()
    except Exception as exc:  # noqa: BLE001 - top-level test client failure.
        print(f"client failed: {exc}", file=sys.stderr, flush=True)
        raise
