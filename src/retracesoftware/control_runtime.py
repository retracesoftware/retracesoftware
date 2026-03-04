"""Control protocol runtime primitives (unwired).

This module provides:
- a generator-based socket event loop (`control_event_loop`)
- a small driver (`Controller`) that advances the generator

It is intentionally not wired into replay protocol execution yet.
"""

from __future__ import annotations

import json
import socket as socket_lib
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional, Protocol, TextIO

import retracesoftware.utils as utils
from retracesoftware.breakpoint import BreakpointSpec, install_breakpoint, install_function_breakpoint

@dataclass
class StopAtBreakpoint:
    breakpoint: dict[str, Any]

@dataclass
class StopAtCursor:
    cursor: dict[str, Any]

ControlExecutionIntent = StopAtBreakpoint | StopAtCursor


class BackstopHitError(Exception):
    def __init__(self, message_index: int = 0, cursor: Optional[dict[str, Any]] = None):
        super().__init__("backstop hit")
        self.message_index = message_index
        self.cursor = cursor or {}


class ReplayEOF(Exception):
    def __init__(self, message_index: int = 0):
        super().__init__("replay reached end of trace")
        self.message_index = message_index


class ParseRequestError(Exception):
    def __init__(self, request_id: str, code: str, message: str):
        super().__init__(message)
        self.request_id = request_id
        self.code = code
        self.message = message


class ControlSocket(Protocol):
    def read_request(self) -> Optional[dict[str, Any]]:
        ...

    def write_response(self, payload: dict[str, Any]) -> None:
        ...

class UnixControlSocket:
    """Line-delimited JSON control socket over Unix domain sockets."""

    def __init__(self, address: str):
        self._sock = socket_lib.socket(socket_lib.AF_UNIX, socket_lib.SOCK_STREAM)
        self._sock.connect(address)
        self._reader: TextIO = self._sock.makefile("r", encoding="utf-8")
        self._writer: TextIO = self._sock.makefile("w", encoding="utf-8")

    def read_request(self) -> Optional[dict[str, Any]]:
        line = self._reader.readline()
        if line == "":
            return None
        text = line.strip()
        if not text:
            raise ParseRequestError("", "invalid_request", "empty request line")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseRequestError("", "invalid_request", "malformed JSON request") from exc
        if not isinstance(value, dict):
            raise ParseRequestError("", "invalid_request", "request must be a JSON object")
        return value

    def write_response(self, payload: dict[str, Any]) -> None:
        self._writer.write(json.dumps(payload, separators=(",", ":")))
        self._writer.write("\n")
        self._writer.flush()

    def close(self) -> None:
        self._reader.close()
        self._writer.close()
        self._sock.close()


class StoppedStateInspector(Protocol):
    def stack(self, params: dict[str, Any]) -> dict[str, Any]:
        ...

    def locals(self, params: dict[str, Any]) -> dict[str, Any]:
        ...

    def inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        ...

    def evaluate(self, params: dict[str, Any]) -> dict[str, Any]:
        ...

    def source_location(self, params: dict[str, Any]) -> dict[str, Any]:
        ...


def _normalize_breakpoint(raw: dict[str, Any]) -> dict[str, Any]:
    if "function" in raw:
        return {"function": raw["function"]}
    file_path = raw.get("file")
    line = raw.get("line")
    if not file_path or line is None:
        return {}
    return {
        "file": file_path,
        "line": int(line),
        "condition": raw.get("condition"),
    }


def _request_id(request: dict[str, Any]) -> str:
    raw = request.get("id")
    if raw is None:
        return ""
    return str(raw)


def _command_name(request: dict[str, Any]) -> str:
    raw = request.get("command")
    if isinstance(raw, str):
        return raw
    raw = request.get("method")
    if isinstance(raw, str):
        return raw
    return ""


def _request_params(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params")
    if isinstance(params, dict):
        return params
    return {}


def _parse_request_fields(request: Any) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(request, dict):
        raise ParseRequestError("", "invalid_request", "request must be an object")
    request_id = _request_id(request)
    command = _command_name(request)
    if not command:
        raise ParseRequestError(request_id, "invalid_request", "missing command")
    return request_id, command, _request_params(request)


def _parse_cursor_param(params: dict[str, Any], request_id: str) -> dict[str, Any]:
    raw_cursor = params.get("cursor")
    if not isinstance(raw_cursor, dict):
        raise ParseRequestError(request_id, "invalid_params", "cursor must be a dict with thread_id and function_counts")
    if "thread_id" not in raw_cursor or "function_counts" not in raw_cursor:
        raise ParseRequestError(request_id, "invalid_params", "cursor requires thread_id and function_counts")
    return raw_cursor


def _write_ok(write_response: Callable[[dict[str, Any]], None], request_id: str, payload: dict[str, Any]) -> None:
    write_response({"id": request_id, "ok": True, "result": payload})


def _write_error(
    write_response: Callable[[dict[str, Any]], None], request_id: str, code: str, message: str
) -> None:
    write_response({"id": request_id, "ok": False, "error": {"code": code, "message": message}})


def _write_event(write_response: Callable[[dict[str, Any]], None], request_id: str, event: str, payload: dict[str, Any]) -> None:
    write_response({"id": request_id, "kind": "event", "event": event, "payload": payload})


def _write_stop(write_response: Callable[[dict[str, Any]], None], stop_result: dict[str, Any]) -> None:
    write_response(
        {
            "kind": "stop",
            "payload": {
                "reason": stop_result["reason"],
                "message_index": stop_result["message_index"],
                "cursor": stop_result["cursor"],
                "thread_cursors": stop_result["thread_cursors"],
            },
        }
    )

def _run_inspection_command(
    inspector: Optional[StoppedStateInspector],
    frame,
    request_id: str,
    command: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if inspector is None:
        raise ParseRequestError(request_id, "not_stopped", "stopped-state inspection is unavailable")

    if command == "stack":

        return inspector.stack(params)
    if command == "locals":
        return inspector.locals(params)
    if command == "inspect":
        return inspector.inspect(params)
    if command == "eval":
        return inspector.evaluate(params)
    if command == "source_location":
        return inspector.source_location(params)

    raise ParseRequestError(request_id, "unknown_command", f"unsupported command: {command}")


def control_event_loop(
    set_backstop: Callable[[int], None],
    control_address: str,
    inspector: Optional[StoppedStateInspector] = None,
    get_message_index: Callable[[], int] = lambda: 0,
) -> Generator[ControlExecutionIntent, dict[str, Any], None]:
    """Blocking control loop that reads commands from a socket-adapter."""
    control_socket = UnixControlSocket(control_address)

    frame = None
    try:
        while True:
            try:
                request = control_socket.read_request()
                if request is None:
                    return

                request_id, command, params = _parse_request_fields(request)
            except ParseRequestError as err:
                _write_error(control_socket.write_response, err.request_id, err.code, err.message)
                continue

            try:
                if command == "hello":
                    _write_ok(control_socket.write_response, request_id, {"protocol": "control", "version": 1})
                    continue

                if command == "set_backstop":
                    value = params.get("message_index")
                    if not isinstance(value, int) or value < 0:
                        raise ParseRequestError(request_id, "invalid_params", "message_index must be >= 0")
                    set_backstop(value)
                    _write_ok(control_socket.write_response, request_id, {"message_index": value})
                    continue

                if command in {"stack", "locals", "inspect", "eval", "source_location"}:
                    result = _run_inspection_command(inspector, frame, request_id, command, params)
                    _write_ok(control_socket.write_response, request_id, result)
                    continue

                if command == "hit_breakpoints":
                    raw = params.get("breakpoint")
                    if not isinstance(raw, dict):
                        raise ParseRequestError(request_id, "invalid_params", "breakpoint must be an object")
                    breakpoint = _normalize_breakpoint(raw)
                    if not breakpoint:
                        raise ParseRequestError(
                            request_id, "invalid_params", "breakpoint requires file+line or function"
                        )
                    max_hits = params.get("max_hits")
                    intent = StopAtBreakpoint(breakpoint=breakpoint)
                    frame = None
                    hit_count = 0
                    while max_hits is None or hit_count < max_hits:
                        cursor_dict = yield intent
                        assert isinstance(cursor_dict, dict)
                        hit_count += 1
                        _write_event(control_socket.write_response, request_id, "breakpoint_hit", {"cursor": cursor_dict, "message_index": get_message_index()})
                    _write_ok(control_socket.write_response, request_id, {"hits": hit_count})
                    continue

                elif command == "run_to_cursor":
                    target = _parse_cursor_param(params, request_id)
                    cursor_dict = yield StopAtCursor(cursor=target)
                    assert isinstance(cursor_dict, dict)
                    _write_stop(control_socket.write_response, {
                        "reason": "cursor",
                        "message_index": 0,
                        "cursor": cursor_dict,
                        "thread_cursors": {},
                    })
                    continue

                if command == "fork":
                    _write_ok(control_socket.write_response, request_id, {"status": "not_implemented"})
                    continue

                if command == "close":
                    _write_ok(control_socket.write_response, request_id, {"closed": True})
                    return

                _write_error(control_socket.write_response, request_id, "unknown_command", f"unsupported command: {command}")

            except BackstopHitError as err:
                _write_stop(control_socket.write_response, {
                    "reason": "backstop",
                    "message_index": err.message_index,
                    "cursor": err.cursor,
                    "thread_cursors": {},
                })

            except ReplayEOF as err:
                _write_stop(control_socket.write_response, {
                    "reason": "eof",
                    "message_index": err.message_index,
                    "cursor": {},
                    "thread_cursors": {},
                })
    finally:
        control_socket.close()

def _resolve_callable(name: str):
    """Resolve a dotted name like '_thread.start_new_thread' to the actual object."""
    module_name, _, attr = name.rpartition(".")
    mod = __import__(module_name)
    for part in module_name.split(".")[1:]:
        mod = getattr(mod, part)
    return getattr(mod, attr)


def register_breakpoint_callback(breakpoint_dict, callback):
    if "function" in breakpoint_dict:
        target = _resolve_callable(breakpoint_dict["function"])
        return install_function_breakpoint(target, callback)
    spec = BreakpointSpec(
        file=breakpoint_dict["file"],
        line=breakpoint_dict["line"],
        condition=breakpoint_dict.get("condition"),
    )
    return install_breakpoint(spec, callback)

def register_cursor_callback(cursor_dict, callback):
    utils.install_call_counter()
    utils.yield_at_call_counts(
        callback,
        cursor_dict["thread_id"],
        tuple(cursor_dict["function_counts"]),
    )

class Controller:

    def __init__(self, control_address: str, inspector: Optional[StoppedStateInspector] = None):
        self._monitor = None
        self._current_breakpoint = None
        self._backstop = None
        self._message_index = 0
        self._done = False

        self.event_loop = control_event_loop(
            set_backstop=self._set_backstop,
            control_address=control_address,
            inspector=inspector,
            get_message_index=lambda: self._message_index,
        )

        try:
            self._handle_intent(next(self.event_loop))
        except StopIteration:
            self._done = True

    def _set_backstop(self, message_index: int):
        self._backstop = message_index

    def on_new_message(self, message):
        self._message_index += 1
        if self._backstop is not None and self._message_index >= self._backstop:
            err = BackstopHitError(
                message_index=self._message_index,
                cursor=utils.cursor_snapshot().to_dict(),
            )
            try:
                self.event_loop.throw(err)
            except StopIteration:
                self._cleanup()

    def _handle_intent(self, intent):
        if isinstance(intent, StopAtBreakpoint):
            if self._current_breakpoint != intent.breakpoint:
                if self._monitor is not None:
                    self._monitor.close()
                self._current_breakpoint = intent.breakpoint
                self._monitor = register_breakpoint_callback(
                    intent.breakpoint, self._on_breakpoint_hit
                )
        elif isinstance(intent, StopAtCursor):
            register_cursor_callback(intent.cursor, self._on_cursor_hit)
        else:
            raise RuntimeError(f"unexpected intent: {intent}")

    def on_replay_finished(self):
        if self._done:
            return
        err = ReplayEOF(message_index=self._message_index)
        try:
            self.event_loop.throw(err)
        except StopIteration:
            self._cleanup()

    def _on_breakpoint_hit(self, cursor_dict):
        try:
            intent = self.event_loop.send(cursor_dict)
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def _on_cursor_hit(self):
        try:
            intent = self.event_loop.send(utils.cursor_snapshot().to_dict())
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def _cleanup(self):
        self._done = True
        self._current_breakpoint = None
        if self._monitor is not None:
            self._monitor.close()
            self._monitor = None
