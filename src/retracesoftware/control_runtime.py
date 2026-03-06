"""Control protocol runtime primitives (unwired).

This module provides:
- a generator-based socket event loop (`control_event_loop`)
- a small driver (`Controller`) that advances the generator

It is intentionally not wired into replay protocol execution yet.
"""

from __future__ import annotations

import json
import os
import sys
import socket as socket_lib
import _thread
from dataclasses import dataclass
from types import CodeType
from typing import Any, Callable, Generator, Optional, Protocol, TextIO

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from retracesoftware.utils.breakpoint import BreakpointSpec, install_breakpoint, install_function_breakpoint, _acquire_tool_id

_real_fork = os.fork

@dataclass
class StopAtBreakpoint:
    breakpoint: dict[str, Any]

@dataclass
class StopAtCursor:
    cursor: dict[str, Any]

@dataclass
class RunToReturn:
    max_call_counter: int | None = None

@dataclass
class NextInstruction:
    pass

ControlExecutionIntent = StopAtBreakpoint | StopAtCursor | RunToReturn | NextInstruction


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

def _parse_json_line(line: str) -> Optional[dict[str, Any]]:
    """Parse a single line of JSON into a request dict, or None on EOF."""
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


def _write_json_line(writer: TextIO, payload: dict[str, Any]) -> None:
    writer.write(json.dumps(payload, separators=(",", ":")))
    writer.write("\n")
    writer.flush()


class UnixControlSocket:
    """Line-delimited JSON control socket over Unix domain sockets."""

    def __init__(self, address: str):
        self._sock = socket_lib.socket(socket_lib.AF_UNIX, socket_lib.SOCK_STREAM)
        self._sock.connect(address)
        self._reader: TextIO = self._sock.makefile("r", encoding="utf-8")
        self._writer: TextIO = self._sock.makefile("w", encoding="utf-8")

    def read_request(self) -> Optional[dict[str, Any]]:
        return _parse_json_line(self._reader.readline())

    def write_response(self, payload: dict[str, Any]) -> None:
        _write_json_line(self._writer, payload)

    def close(self) -> None:
        self._reader.close()
        self._writer.close()
        self._sock.close()


class StdioControlSocket:
    """Line-delimited JSON control socket over stdin/stdout."""

    def __init__(self, reader: TextIO = None, writer: TextIO = None):
        self._reader = reader or sys.stdin
        self._writer = writer or sys.stdout

    def read_request(self) -> Optional[dict[str, Any]]:
        return _parse_json_line(self._reader.readline())

    def write_response(self, payload: dict[str, Any]) -> None:
        _write_json_line(self._writer, payload)

    def close(self) -> None:
        pass


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


def _build_instruction_lineno_list(code: CodeType) -> list[int]:
    """Build a list mapping instruction index to source line number.

    Index ``i`` corresponds to bytecode offset ``i * 2``.  Caller looks up
    a cursor's ``f_lasti`` via ``linenos[f_lasti // 2]``.
    """
    size = len(code.co_code) // 2
    linenos = [0] * size
    for start, end, lineno in code.co_lines():
        if lineno is None:
            continue
        for offset in range(start, end, 2):
            idx = offset // 2
            if idx < size:
                linenos[idx] = lineno
    return linenos


def control_event_loop(
    set_backstop: Callable[[int], None],
    control_socket: ControlSocket,
    inspector: Optional[StoppedStateInspector] = None,
    get_message_index: Callable[[], int] = lambda: 0,
    get_instruction_map: Optional[Callable[[], Optional[list[int]]]] = None,
    on_before_fork: Optional[Callable[[], Any]] = None,
    on_after_fork: Optional[Callable[[Any], None]] = None,
) -> Generator[ControlExecutionIntent, dict[str, Any], None]:
    """Blocking control loop that reads commands from a control socket."""

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

                if command == "instruction_to_lineno":
                    if get_instruction_map is None:
                        raise ParseRequestError(
                            request_id, "not_available",
                            "instruction_to_lineno is not supported",
                        )
                    linenos = get_instruction_map()
                    if linenos is None:
                        raise ParseRequestError(
                            request_id, "not_stopped",
                            "no code object available (stop at a breakpoint or run run_to_return first)",
                        )
                    _write_ok(control_socket.write_response, request_id, {"linenos": linenos})
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

                elif command == "run_to_return":
                    max_cc = params.get("max_call_counter")
                    if max_cc is not None and (not isinstance(max_cc, int) or max_cc < 0):
                        raise ParseRequestError(
                            request_id, "invalid_params",
                            "max_call_counter must be a non-negative integer",
                        )
                    intent = RunToReturn(max_call_counter=max_cc)
                    frame = None
                    last_cursor: dict[str, Any] = {}
                    while True:
                        result = yield intent
                        if isinstance(result, str):
                            _write_stop(control_socket.write_response, {
                                "reason": result,
                                "message_index": get_message_index(),
                                "cursor": last_cursor,
                                "thread_cursors": {},
                            })
                            break
                        last_cursor = result
                        _write_event(
                            control_socket.write_response, request_id,
                            "cursor", {
                                "cursor": result,
                                "message_index": get_message_index(),
                            },
                        )
                    continue

                if command == "next_instruction":
                    result = yield NextInstruction()
                    if isinstance(result, str):
                        _write_stop(control_socket.write_response, {
                            "reason": result,
                            "message_index": get_message_index(),
                            "cursor": {},
                            "thread_cursors": {},
                        })
                    else:
                        assert isinstance(result, dict)
                        _write_stop(control_socket.write_response, {
                            "reason": "instruction",
                            "message_index": get_message_index(),
                            "cursor": result,
                            "thread_cursors": {},
                        })
                    continue

                if command == "fork":
                    socket_path = params.get("socket_path")
                    fork_id = params.get("fork_id", "")
                    if not socket_path:
                        raise ParseRequestError(
                            request_id, "invalid_params", "fork requires socket_path"
                        )

                    saved = on_before_fork() if on_before_fork else None

                    child_pid = _real_fork()

                    if on_after_fork:
                        on_after_fork(saved)

                    if child_pid > 0:
                        _write_ok(control_socket.write_response, request_id, {
                            "pid": child_pid, "fork_id": fork_id,
                        })
                    else:
                        control_socket.close()
                        control_socket = UnixControlSocket(socket_path)
                        control_socket.write_response({
                            "type": "event", "event": "fork_hello",
                            "payload": {"fork_id": fork_id, "pid": os.getpid()},
                        })
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


def register_breakpoint_callback(breakpoint_dict, callback, log=None):
    if "function" in breakpoint_dict:
        target = _resolve_callable(breakpoint_dict["function"])
        return install_function_breakpoint(target, callback)
    spec = BreakpointSpec(
        file=breakpoint_dict["file"],
        line=breakpoint_dict["line"],
        condition=breakpoint_dict.get("condition"),
    )
    return install_breakpoint(spec, callback, log=log)

def register_cursor_callback(cursor_dict, callback):
    utils.install_call_counter()
    utils.yield_at_call_counts(
        callback,
        cursor_dict["thread_id"],
        tuple(cursor_dict["function_counts"]),
    )

def _find_user_frame():
    """Walk the call stack to find the first frame outside retracesoftware internals."""
    frame = sys._getframe(1)
    while frame is not None:
        fn = frame.f_code.co_filename
        if "retracesoftware" not in fn:
            return frame
        frame = frame.f_back
    return None


class Controller:

    def __init__(
        self,
        control_socket: ControlSocket,
        inspector: Optional[StoppedStateInspector] = None,
        on_before_fork: Optional[Callable[[], Any]] = None,
        on_after_fork: Optional[Callable[[Any], None]] = None,
        disable_for: Optional[Callable] = None,
    ):
        self._disable_for = functional.sequence(utils.call_counter_disable_for, disable_for)
        self._control_socket = control_socket
        self._in_control_log = False
        self._monitor = None
        self._trace_monitor = None
        self._current_breakpoint = None
        self._backstop = None
        self._message_index = 0
        self._done = False
        self._stopped_frame = None
        self._last_code: CodeType | None = None

        self.event_loop = control_event_loop(
            set_backstop=self._set_backstop,
            control_socket=control_socket,
            inspector=inspector,
            get_message_index=lambda: self._message_index,
            get_instruction_map=self._get_instruction_map,
            on_before_fork=on_before_fork,
            on_after_fork=on_after_fork,
        )

        try:
            self._handle_intent(next(self.event_loop))
        except StopIteration:
            self._done = True

    def _set_backstop(self, message_index: int):
        self._backstop = message_index

    def on_new_message(self, message):
        self._message_index += 1
        if self._done:
            return
        if self._backstop is not None and self._message_index >= self._backstop:
            err = BackstopHitError(
                message_index=self._message_index,
                cursor=utils.cursor_snapshot().to_dict(),
            )
            try:
                self.event_loop.throw(err)
            except StopIteration:
                self._cleanup()

    def _control_log(self, message: str) -> None:
        """Send a diagnostic log message over the control socket."""
        if self._in_control_log:
            return
        self._in_control_log = True
        try:
            _write_event(self._control_socket.write_response, "", "log", {"message": message})
        finally:
            self._in_control_log = False

    def _handle_intent(self, intent):
        if isinstance(intent, StopAtBreakpoint):
            if self._current_breakpoint != intent.breakpoint:
                if self._monitor is not None:
                    self._monitor.close()
                self._current_breakpoint = intent.breakpoint
                self._monitor = register_breakpoint_callback(
                    intent.breakpoint, self._disable_for(self._on_breakpoint_hit),
                    log=self._disable_for(self._control_log),
                )

        elif isinstance(intent, StopAtCursor):
            register_cursor_callback(intent.cursor, self._disable_for(self._on_cursor_hit))

        elif isinstance(intent, RunToReturn):
            self._install_run_to_return(intent)

        elif isinstance(intent, NextInstruction):
            self._install_next_instruction()

        else:
            raise RuntimeError(f"unexpected intent: {intent}")

    def _install_run_to_return(self, intent: RunToReturn):
        thread_id = _thread.get_ident()
        counters = utils.current_call_counts()

        if self._trace_monitor is not None:
            self._trace_monitor.close()

        self._trace_monitor = utils.trace_function_instructions(
            thread_id, counters, self._disable_for(self._on_instruction_hit),
            target_frame=self._stopped_frame,
            on_complete=self._disable_for(self._on_trace_complete),
        )

        if intent.max_call_counter is not None:
            limit_counters = counters + (intent.max_call_counter,)

            def _on_limit():
                if self._trace_monitor is not None:
                    self._trace_monitor.close()
                    self._trace_monitor = None
                self._send_reason("call_counter")

            utils.watch(thread_id, limit_counters, on_start=self._disable_for(_on_limit))

    def _install_next_instruction(self):
        if self._trace_monitor is not None:
            self._trace_monitor.close()

        thread_id = _thread.get_ident()
        tool_id = _acquire_tool_id("retrace_next_instr")
        E = sys.monitoring.events
        monitor = utils.InstructionMonitor(tool_id)
        self._trace_monitor = monitor

        def on_hit(code, offset):
            if _thread.get_ident() != thread_id:
                return
            monitor.close()
            self._trace_monitor = None
            self._last_code = code
            self._stopped_frame = _find_user_frame()
            cursor_dict = {
                "thread_id": thread_id,
                "function_counts": list(utils.current_call_counts()),
                "f_lasti": offset,
            }
            try:
                intent = self.event_loop.send(cursor_dict)
                self._handle_intent(intent)
            except StopIteration:
                self._cleanup()

        sys.monitoring.register_callback(
            tool_id, E.INSTRUCTION, self._disable_for(on_hit)
        )
        sys.monitoring.set_events(tool_id, E.INSTRUCTION)

    def _get_instruction_map(self) -> list[int] | None:
        code = None
        if self._stopped_frame is not None:
            code = self._stopped_frame.f_code
        elif self._last_code is not None:
            code = self._last_code
        if code is None:
            return None
        return _build_instruction_lineno_list(code)

    def _on_instruction_hit(self, code, offset):
        self._last_code = code
        cursor_dict = {
            "thread_id": _thread.get_ident(),
            "function_counts": list(utils.current_call_counts()),
            "f_lasti": offset,
        }
        try:
            intent = self.event_loop.send(cursor_dict)
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def _on_trace_complete(self):
        self._trace_monitor = None
        self._send_reason("return")

    def _send_reason(self, reason: str):
        try:
            intent = self.event_loop.send(reason)
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def on_replay_finished(self):
        if self._done:
            return
        err = ReplayEOF(message_index=self._message_index)
        try:
            self.event_loop.throw(err)
        except (StopIteration, ReplayEOF):
            self._cleanup()

    def _on_breakpoint_hit(self, cursor_dict):
        self._stopped_frame = _find_user_frame()
        try:
            intent = self.event_loop.send(cursor_dict)
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def _on_cursor_hit(self):
        self._stopped_frame = _find_user_frame()
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
        if self._trace_monitor is not None:
            self._trace_monitor.close()
            self._trace_monitor = None
        self._stopped_frame = None
