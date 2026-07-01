"""Minimal DAP client reproducing issue #75 bad-stop stackTrace behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def read_message(stream) -> dict:
    content_length = -1
    while True:
        line = stream.readline()
        if not line:
            raise RuntimeError("dap stream closed")
        text = line.decode("ascii").strip()
        if text == "":
            break
        if text.lower().startswith("content-length:"):
            content_length = int(text.split(":", 1)[1].strip())
    raw = stream.read(content_length)
    return json.loads(raw.decode("utf-8"))


def read_until_response(stream, request_seq: int, command: str) -> dict:
    while True:
        message = read_message(stream)
        if (
            message.get("type") == "response"
            and message.get("request_seq") == request_seq
            and message.get("command") == command
        ):
            return message


def read_until_event(stream, event: str) -> dict:
    while True:
        message = read_message(stream)
        if message.get("type") == "event" and message.get("event") == event:
            return message


def read_until_stopped_or_terminated(stream) -> dict:
    while True:
        message = read_message(stream)
        if message.get("type") != "event":
            continue
        if message.get("event") in {"stopped", "terminated"}:
            return message


def send(proc, command: str, arguments: dict | None = None, seq: int = 1) -> tuple[int, dict]:
    msg = {"seq": seq, "type": "request", "command": command}
    if arguments is not None:
        msg["arguments"] = arguments
    body = json.dumps(msg).encode("utf-8")
    proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    proc.stdin.write(body)
    proc.stdin.flush()
    return seq + 1, read_until_response(proc.stdout, seq, command)


def main() -> int:
    pidfile, pytest_path, pytest_line = sys.argv[1:4]
    replay_bin = os.environ.get("RETRACE_REPLAY_BIN") or str(
        Path(__file__).resolve().parents[2] / ".retrace-replay-bin"
    )
    proc = subprocess.Popen(
        [str(replay_bin), "--dap", pidfile],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout

    seq = 1
    seq, _ = send(proc, "initialize", {"clientID": "issue75-probe", "adapterID": "retrace"}, seq)
    read_until_event(proc.stdout, "initialized")
    seq, _ = send(
        proc,
        "launch",
        {"type": "retrace", "request": "launch", "recording": pidfile},
        seq,
    )
    seq, _ = send(proc, "setExceptionBreakpoints", {"filters": []}, seq)
    seq, _ = send(
        proc,
        "setBreakpoints",
        {
            "source": {"path": pytest_path},
            "breakpoints": [{"line": int(pytest_line)}],
        },
        seq,
    )
    seq, _ = send(proc, "configurationDone", {}, seq)
    read_until_event(proc.stdout, "stopped")
    seq, _ = send(proc, "continue", {"threadId": 1}, seq)
    post_continue = read_until_stopped_or_terminated(proc.stdout)

    result: dict[str, object] = {
        "postContinueEvent": post_continue.get("event"),
    }
    if post_continue.get("event") == "stopped":
        seq, stack = send(proc, "stackTrace", {"threadId": 1}, seq)
        body = stack.get("body") if isinstance(stack.get("body"), dict) else {}
        retrace = body.get("retrace") if isinstance(body.get("retrace"), dict) else {}
        result["stackTrace"] = {
            "success": stack.get("success"),
            "message": stack.get("message"),
            "frame_count": len(body.get("stackFrames") or []),
            "retrace": retrace,
        }
    send(proc, "disconnect", {}, seq)
    proc.stdin.close()
    proc.wait(timeout=10)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
