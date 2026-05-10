"""HTTP load generator for the Datasette server dockertest."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from urllib.parse import urlparse
from urllib.request import urlopen


BASE_URL = os.environ.get("SERVER_URL", "http://localhost:5000")


def get_json(path: str):
    with urlopen(f"{BASE_URL}{path}", timeout=5) as response:
        assert response.status == 200, (path, response.status)
        payload = response.read().decode("utf-8")
    print(f"GET {path} -> {len(payload)} bytes", flush=True)
    return json.loads(payload)


def get_text(path: str) -> str:
    with urlopen(f"{BASE_URL}{path}", timeout=5) as response:
        assert response.status == 200, (path, response.status)
        payload = response.read().decode("utf-8")
    print(f"GET {path} -> {len(payload)} bytes", flush=True)
    return payload


def wait_for_server() -> None:
    parsed = urlparse(BASE_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                pass
            print("Datasette server is ready", flush=True)
            return
        except Exception as exc:  # noqa: BLE001 - load generator reports context.
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Datasette server did not become ready: {last_error}")


def run_load() -> None:
    print("=== datasette_server_test client ===", flush=True)
    wait_for_server()

    versions = get_json("/-/versions.json")
    database_page = get_text("/datasette-demo")

    assert versions["datasette"]["version"], versions
    assert "<html" in database_page.lower(), database_page[:200]
    assert "datasette-demo" in database_page, database_page[:500]
    print("Datasette load generation complete", flush=True)


if __name__ == "__main__":
    try:
        run_load()
    except Exception as exc:  # noqa: BLE001 - top-level test client failure.
        print(f"client failed: {exc}", file=sys.stderr, flush=True)
        raise
