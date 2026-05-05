"""Regression coverage for the medium VS Code DAP breakpoint repro.

VS Code sends ``setBreakpoints`` once per source file. The medium repro sets
breakpoints in ``main.py`` and ``service.py`` back-to-back, then continues from
entry. The first user-code breakpoint should be ``main.py:9`` because that line
executes before the service loop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest


def test_medium_vscode_dap_keeps_breakpoints_across_source_files(tmp_path: Path):
    """A dummy VS Code DAP controller should stop first at main.py:9."""

    paths = _write_medium_app(tmp_path)
    recording = tmp_path / "medium.retrace"
    env = _clean_env()

    record = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            str(paths["main"]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    assert record.returncode == 0, (
        f"record failed (exit {record.returncode})\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "A100: Ada owes GBP 22.50 [ok]" in record.stdout

    replay_bin = _replay_binary()
    with DummyVSCodeDAPController(replay_bin, recording, cwd=tmp_path, env=env) as controller:
        controller.initialize()
        controller.launch(recording)

        main_req = controller.set_breakpoints(paths["main"], [9])
        service_req = controller.set_breakpoints(paths["service"], [17])

        main_response = controller.response_for(main_req)
        service_response = controller.response_for(service_req)
        assert _verified_count(main_response) == 1
        assert _verified_count(service_response) == 1

        controller.configuration_done()
        controller.continue_thread()
        top_frame = controller.top_stack_frame()

    assert top_frame["source"]["path"] == str(paths["main"])
    assert top_frame["line"] == 9


class DummyVSCodeDAPController:
    """Small DAP client that mimics VS Code's breakpoint setup sequence."""

    def __init__(
        self,
        replay_bin: Path,
        recording: Path,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> None:
        self.proc = subprocess.Popen(
            [str(replay_bin), "--recording", str(recording), "--dap"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.seq = 0
        self.buffer = b""
        self.messages: list[dict] = []

    def __enter__(self) -> "DummyVSCodeDAPController":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.proc.poll() is None:
                self.send("disconnect")
                self.response("disconnect", timeout=3)
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def initialize(self) -> None:
        self.send("initialize", {"clientID": "dummy-vscode", "adapterID": "retrace"})
        self.response("initialize")
        self.event("initialized")

    def launch(self, recording: Path) -> None:
        self.send(
            "launch",
            {
                "type": "retrace",
                "request": "launch",
                "name": "Medium VS Code DAP regression",
                "recording": str(recording),
            },
        )
        self.response("launch")

    def set_breakpoints(self, source: Path, lines: list[int]) -> int:
        return self.send(
            "setBreakpoints",
            {
                "source": {"name": source.name, "path": str(source)},
                "lines": lines,
                "breakpoints": [{"line": line} for line in lines],
            },
        )

    def configuration_done(self) -> None:
        self.send("configurationDone")
        self.response("configurationDone")
        stopped = self.event("stopped")
        assert stopped.get("body", {}).get("reason") == "entry"

    def continue_thread(self) -> None:
        self.send("continue", {"threadId": 1})
        self.response("continue")
        stopped = self.event("stopped", timeout=30)
        assert stopped.get("body", {}).get("reason") == "breakpoint"

    def top_stack_frame(self) -> dict:
        self.send("stackTrace", {"threadId": 1, "startFrame": 0, "levels": 5})
        response = self.response("stackTrace")
        frames = response.get("body", {}).get("stackFrames", [])
        assert frames, f"stackTrace returned no frames; messages={self.messages!r}"
        return frames[0]

    def send(self, command: str, arguments: dict | None = None) -> int:
        self.seq += 1
        message: dict[str, object] = {
            "seq": self.seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            message["arguments"] = arguments
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + body)
        self.proc.stdin.flush()
        return self.seq

    def response(self, command: str, timeout: float = 20.0) -> dict:
        message = self.wait_for(
            lambda msg: msg.get("type") == "response" and msg.get("command") == command,
            timeout=timeout,
        )
        assert message.get("success") is True, (
            f"{command} failed: {message}\n"
            f"stderr:\n{self.stderr()}"
        )
        return message

    def response_for(self, request_seq: int, timeout: float = 20.0) -> dict:
        message = self.wait_for(
            lambda msg: msg.get("type") == "response"
            and msg.get("request_seq") == request_seq,
            timeout=timeout,
        )
        assert message.get("success") is True, (
            f"request {request_seq} failed: {message}\n"
            f"stderr:\n{self.stderr()}"
        )
        return message

    def event(self, name: str, timeout: float = 20.0) -> dict:
        return self.wait_for(
            lambda msg: msg.get("type") == "event" and msg.get("event") == name,
            timeout=timeout,
        )

    def wait_for(self, predicate, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.read(max(0.1, deadline - time.time()))
            self.messages.append(message)
            if predicate(message):
                return message
        raise TimeoutError(
            f"condition not met after {timeout}s\n"
            f"messages:\n{json.dumps(self.messages, indent=2)}\n"
            f"stderr:\n{self.stderr()}"
        )

    def read(self, timeout: float) -> dict:
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

    def _fill(self, deadline: float) -> None:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        readable, _, _ = select.select([fd], [], [], max(0.0, deadline - time.time()))
        if not readable:
            raise TimeoutError(f"timed out waiting for DAP output\nstderr:\n{self.stderr()}")
        chunk = os.read(fd, 65536)
        if not chunk:
            raise EOFError(f"DAP process closed stdout\nstderr:\n{self.stderr()}")
        self.buffer += chunk

    def stderr(self) -> str:
        if self.proc.stderr is None:
            return ""
        fd = self.proc.stderr.fileno()
        chunks = []
        while True:
            readable, _, _ = select.select([fd], [], [], 0)
            if not readable:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace")


def _write_medium_app(tmp_path: Path) -> dict[str, Path]:
    files = {
        "repository": tmp_path / "repository.py",
        "formatter": tmp_path / "formatter.py",
        "service": tmp_path / "service.py",
        "main": tmp_path / "main.py",
    }
    files["repository"].write_text(
        (
            "class OrderRepository:\n"
            "    def load_orders(self):\n"
            "        return [\n"
            "            {'id': 'A100', 'customer': 'Ada', 'items': [12, 8, 5], 'vip': True},\n"
            "            {'id': 'B200', 'customer': 'Grace', 'items': [30, 20], 'vip': False},\n"
            "            {'id': 'C300', 'customer': 'Linus', 'items': [], 'vip': True},\n"
            "        ]\n"
        ),
        encoding="utf-8",
    )
    files["formatter"].write_text(
        (
            "def format_summary(order_id, customer, total, status):\n"
            "    return f'{order_id}: {customer} owes GBP {total:.2f} [{status}]'\n"
        ),
        encoding="utf-8",
    )
    files["service"].write_text(
        (
            "from formatter import format_summary\n"
            "\n"
            "\n"
            "class EmptyOrderError(Exception):\n"
            "    pass\n"
            "\n"
            "\n"
            "class OrderService:\n"
            "    def __init__(self, repository):\n"
            "        self.repository = repository\n"
            "\n"
            "    def calculate_total(self, order):\n"
            "        if not order['items']:\n"
            "            raise EmptyOrderError(order['id'])\n"
            "\n"
            "        subtotal = 0\n"
            "        for price in order['items']:\n"
            "            subtotal += price\n"
            "\n"
            "        if order['vip']:\n"
            "            subtotal *= 0.9\n"
            "\n"
            "        if subtotal >= 50:\n"
            "            subtotal -= 5\n"
            "\n"
            "        return subtotal\n"
            "\n"
            "    def build_summaries(self):\n"
            "        summaries = []\n"
            "\n"
            "        for order in self.repository.load_orders():\n"
            "            try:\n"
            "                total = self.calculate_total(order)\n"
            "                status = 'ok'\n"
            "            except EmptyOrderError:\n"
            "                total = 0\n"
            "                status = 'empty'\n"
            "\n"
            "            summaries.append(\n"
            "                format_summary(order['id'], order['customer'], total, status)\n"
            "            )\n"
            "\n"
            "        return summaries\n"
        ),
        encoding="utf-8",
    )
    files["main"].write_text(
        (
            "from repository import OrderRepository\n"
            "from service import OrderService\n"
            "\n"
            "\n"
            "def main():\n"
            "    repository = OrderRepository()\n"
            "    service = OrderService(repository)\n"
            "\n"
            "    summaries = service.build_summaries()\n"
            "\n"
            "    for line in summaries:\n"
            "        print(line)\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        encoding="utf-8",
    )
    return files


def _verified_count(response: dict) -> int:
    return sum(
        1
        for breakpoint in response.get("body", {}).get("breakpoints", [])
        if breakpoint.get("verified") is True
    )


def _replay_binary() -> Path:
    replay_bin = Path(sys.executable).with_name("replay")
    if not replay_bin.exists():
        pytest.skip(f"Go replay binary not installed next to {sys.executable}")
    return replay_bin


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("RETRACE_") or key in {
            "MESONPY_EDITABLE_SKIP",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        }:
            env.pop(key, None)
    env["PYTHONFAULTHANDLER"] = "1"
    return env
