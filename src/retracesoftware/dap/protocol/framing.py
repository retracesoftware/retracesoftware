"""DAP message framing: Content-Length header over a byte stream."""

from __future__ import annotations

import json
from typing import Any


def read_message(rfile) -> dict[str, Any] | None:
    """Read one DAP message (Content-Length framed JSON) from *rfile*.

    Returns the parsed JSON body, or None on EOF.
    """
    content_length = -1

    while True:
        line = rfile.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.startswith(b"Content-Length:"):
            content_length = int(line[len(b"Content-Length:"):].strip())

    if content_length < 0:
        return None

    body = rfile.read(content_length)
    if len(body) < content_length:
        return None

    return json.loads(body)


def write_message(wfile, body: dict[str, Any]) -> None:
    """Write one DAP message (Content-Length framed JSON) to *wfile*."""
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
    wfile.write(header)
    wfile.write(payload)
    wfile.flush()
