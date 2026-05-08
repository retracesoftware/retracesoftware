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

    items = get_json("/datasette-demo/items.json?_shape=array")
    audit_log = get_json("/datasette-demo/audit_log.json?_shape=array")
    metadata = get_json("/datasette-demo.json")

    assert len(items) == 3, items
    assert len(audit_log) == 2, audit_log
    assert "tables" in metadata, metadata
    print("Datasette load generation complete", flush=True)


if __name__ == "__main__":
    try:
        run_load()
    except Exception as exc:  # noqa: BLE001 - top-level test client failure.
        print(f"client failed: {exc}", file=sys.stderr, flush=True)
        raise
