from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time


CASE_DIR = Path(__file__).resolve().parent
RECORDING = CASE_DIR / "medium.retrace"
MAIN = CASE_DIR / "main.py"
SERVICE = CASE_DIR / "service.py"


class DAPClient:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.seq = 0
        self.buffer = b""

    def send(self, command: str, arguments: dict | None = None) -> int:
        self.seq += 1
        message: dict[str, object] = {
            "seq": self.seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            message["arguments"] = arguments
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + body)
        self.proc.stdin.flush()
        return self.seq

    def _fill(self, deadline: float) -> None:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        readable, _, _ = select.select([fd], [], [], max(0.0, deadline - time.time()))
        if not readable:
            raise TimeoutError("timed out waiting for DAP output")
        chunk = os.read(fd, 65536)
        if not chunk:
            raise EOFError("DAP process closed stdout")
        self.buffer += chunk

    def read(self, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while b"\r\n\r\n" not in self.buffer:
            self._fill(deadline)
        header, rest = self.buffer.split(b"\r\n\r\n", 1)
        length = None
        for line in header.decode("ascii").split("\r\n"):
            name, _, value = line.partition(":")
            if name.lower() == "content-length":
                length = int(value.strip())
                break
        if length is None:
            raise RuntimeError(f"missing Content-Length header: {header!r}")
        while len(rest) < length:
            self._fill(deadline)
            header, rest = self.buffer.split(b"\r\n\r\n", 1)
        body = rest[:length]
        self.buffer = rest[length:]
        return json.loads(body.decode("utf-8"))

    def wait_for(self, predicate, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            message = self.read(max(0.1, deadline - time.time()))
            seen.append(message)
            if predicate(message):
                return message
        raise TimeoutError(f"condition not met; messages={seen}")

    def response(self, command: str, timeout: float = 20.0) -> dict:
        message = self.wait_for(
            lambda m: m.get("type") == "response" and m.get("command") == command,
            timeout,
        )
        if message.get("success") is not True:
            raise RuntimeError(f"{command} failed: {message}")
        return message

    def response_for(self, request_seq: int, timeout: float = 20.0) -> dict:
        message = self.wait_for(
            lambda m: m.get("type") == "response" and m.get("request_seq") == request_seq,
            timeout,
        )
        if message.get("success") is not True:
            raise RuntimeError(f"request {request_seq} failed: {message}")
        return message


def replay_binary_from_trace(path: Path) -> str:
    first_line = path.read_bytes().split(b"\n", 1)[0].decode("ascii")
    if not first_line.startswith("#!"):
        raise RuntimeError(f"trace has no shebang: {path}")
    shebang = first_line[2:]
    suffix = " --recording"
    if shebang.endswith(suffix):
        return shebang[: -len(suffix)]
    return shebang.split()[0]


def verified_count(response: dict) -> int:
    breakpoints = response.get("body", {}).get("breakpoints", [])
    return sum(1 for bp in breakpoints if bp.get("verified") is True)


def main() -> int:
    if not RECORDING.exists():
        print("medium.retrace does not exist; run ./record.sh first", file=sys.stderr)
        return 2

    binary = replay_binary_from_trace(RECORDING)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    venv_bin = CASE_DIR / ".venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

    proc = subprocess.Popen(
        [binary, "--recording", str(RECORDING), "--dap"],
        cwd=CASE_DIR,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = DAPClient(proc)

    try:
        client.send("initialize", {"clientID": "medium-repro", "adapterID": "retrace"})
        client.response("initialize")
        client.wait_for(lambda m: m.get("type") == "event" and m.get("event") == "initialized")

        client.send("launch", {"type": "retrace", "request": "launch", "recording": str(RECORDING)})
        client.response("launch")

        main_req = client.send(
            "setBreakpoints",
            {
                "source": {"name": MAIN.name, "path": str(MAIN)},
                "lines": [9],
                "breakpoints": [{"line": 9}],
            },
        )
        service_req = client.send(
            "setBreakpoints",
            {
                "source": {"name": SERVICE.name, "path": str(SERVICE)},
                "lines": [18],
                "breakpoints": [{"line": 18}],
            },
        )

        main_response = client.response_for(main_req, timeout=30.0)
        service_response = client.response_for(service_req, timeout=30.0)

        print(f"main.py:9 verified breakpoints: {verified_count(main_response)}")
        print(f"service.py:18 verified breakpoints: {verified_count(service_response)}")

        if verified_count(main_response) != 1 or verified_count(service_response) != 1:
            print("FAIL concurrent breakpoint setup")
            print(json.dumps({"main": main_response, "service": service_response}, indent=2))
            return 1

        print("PASS concurrent breakpoint setup")
        return 0
    except Exception as exc:
        print(f"FAIL concurrent breakpoint setup: {exc}")
        if proc.stderr is not None:
            print(proc.stderr.read().decode("utf-8", "replace"))
        return 1
    finally:
        if proc.poll() is None:
            client.send("disconnect")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
