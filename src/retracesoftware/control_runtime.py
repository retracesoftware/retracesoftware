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
import retracesoftware.cursor as cursor
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
    function_counts: tuple[int, ...]
    max_call_counter: int | None = None

@dataclass
class NextInstruction:
    pass

@dataclass
class WaitForThreadChange:
    pass

ControlExecutionIntent = StopAtBreakpoint | StopAtCursor | RunToReturn | NextInstruction | WaitForThreadChange


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


class FrameInspector:
    """StoppedStateInspector backed by a live Python frame object."""

    def __init__(self, frame):
        self._frame = frame

    @staticmethod
    def _frame_lineno(frame) -> int:
        lineno = frame.f_lineno
        if lineno > 0:
            return lineno
        lasti = frame.f_lasti
        for start, end, line in frame.f_code.co_lines():
            if line is not None and line > 0 and start <= lasti < end:
                return line
        return frame.f_code.co_firstlineno or 1

    def stack(self, params: dict[str, Any]) -> dict[str, Any]:
        frames = []
        f = self._frame
        while f is not None:
            frames.append({
                "filename": f.f_code.co_filename,
                "function": f.f_code.co_name,
                "line": self._frame_lineno(f),
            })
            f = f.f_back
        return {"frames": frames}

    def locals(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._frame is None:
            return {"variables": []}
        result = []
        for name, value in self._frame.f_locals.items():
            try:
                val_repr = repr(value)
            except Exception:
                val_repr = "<repr failed>"
            result.append({
                "name": name,
                "value": val_repr,
                "type": type(value).__name__,
            })
        return {"variables": result}

    def inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def evaluate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def source_location(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._frame is None:
            return {}
        return {
            "filename": self._frame.f_code.co_filename,
            "line": self._frame_lineno(self._frame),
        }


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
        raise ParseRequestError(request_id, "invalid_params", "cursor must be a dict with function_counts")
    if "function_counts" not in raw_cursor:
        raise ParseRequestError(request_id, "invalid_params", "cursor requires function_counts")
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


def _build_instruction_info(code: CodeType) -> tuple[list[int], list[int]]:
    """Build per-instruction lineno and sequential_before arrays.

    Index ``i`` corresponds to bytecode offset ``i * 2``.

    ``sequential_before[i]`` is the number of immediately preceding
    instructions that are safe to step back through sequentially (no
    jump targets, generator/coroutine resume points, function entry,
    or child-call boundaries in between).  A value of 0 means this
    instruction is an entry point and backward sequential stepping is
    not safe.
    """
    import dis
    import opcode

    raw = code.co_code
    size = len(raw) // 2

    # --- linenos ---
    linenos = [0] * size
    for start, end, lineno in code.co_lines():
        if lineno is None:
            continue
        for offset in range(start, end, 2):
            idx = offset // 2
            if idx < size:
                linenos[idx] = lineno

    # --- entry points (non-sequential) ---
    entry_points: set[int] = {0}

    for offset in dis.findlabels(raw):
        entry_points.add(offset)

    resume_op = opcode.opmap.get("RESUME")
    if resume_op is not None:
        for i in range(size):
            if raw[i * 2] == resume_op:
                entry_points.add(i * 2)

    # Mark instructions following CALL-family opcodes as entry points.
    # After a child call returns, FunctionCounts has changed, so the
    # fast path must not scan backward across that boundary.
    call_opcodes = frozenset(
        opcode.opmap[n]
        for n in ("CALL", "CALL_FUNCTION_EX")
        if n in opcode.opmap
    )
    instrs = list(dis.get_instructions(code))
    for i, instr in enumerate(instrs):
        if instr.opcode in call_opcodes and i + 1 < len(instrs):
            entry_points.add(instrs[i + 1].offset)

    # --- sequential_before ---
    sequential_before = [0] * size
    for i in range(size):
        if i * 2 in entry_points:
            sequential_before[i] = 0
        else:
            sequential_before[i] = (sequential_before[i - 1] + 1) if i > 0 else 0

    return linenos, sequential_before


def control_event_loop(
    set_backstop: Callable[[int], None],
    control_socket: ControlSocket,
    get_inspector: Callable[[], Optional[StoppedStateInspector]] = lambda: None,
    get_message_index: Callable[[], int] = lambda: 0,
    get_thread_id: Callable[[], Any] = lambda: None,
    get_instruction_map: Optional[Callable[[], Optional[tuple[list[int], list[int]]]]] = None,
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
                    result = _run_inspection_command(get_inspector(), frame, request_id, command, params)
                    _write_ok(control_socket.write_response, request_id, result)
                    continue

                if command == "instruction_to_lineno":
                    if get_instruction_map is None:
                        raise ParseRequestError(
                            request_id, "not_available",
                            "instruction_to_lineno is not supported",
                        )
                    info = get_instruction_map()
                    if info is None:
                        raise ParseRequestError(
                            request_id, "not_stopped",
                            "no code object available (stop at a breakpoint or run run_to_return first)",
                        )
                    linenos, sequential_before = info
                    _write_ok(control_socket.write_response, request_id, {
                        "linenos": linenos,
                        "sequential_before": sequential_before,
                    })
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
                    raw_fc = params.get("function_counts")
                    if not isinstance(raw_fc, (list, tuple)) or not raw_fc:
                        raise ParseRequestError(
                            request_id, "invalid_params",
                            "run_to_return requires non-empty function_counts",
                        )
                    max_cc = params.get("max_call_counter")
                    if max_cc is not None and (not isinstance(max_cc, int) or max_cc < 0):
                        raise ParseRequestError(
                            request_id, "invalid_params",
                            "max_call_counter must be a non-negative integer",
                        )
                    intent = RunToReturn(function_counts=tuple(raw_fc), max_call_counter=max_cc)
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

                if command == "wait_for_thread":
                    target_tid = params.get("thread_id")
                    if target_tid is None:
                        raise ParseRequestError(
                            request_id, "invalid_params",
                            "wait_for_thread requires thread_id",
                        )
                    while target_tid != get_thread_id():
                        yield WaitForThreadChange()
                    _write_ok(control_socket.write_response, request_id, {
                        "thread_id": get_thread_id(),
                    })
                    continue

                if command == "fork":
                    socket_path = params.get("socket_path")
                    fork_id = params.get("fork_id", "")
                    if not socket_path:
                        raise ParseRequestError(
                            request_id, "invalid_params", "fork requires socket_path"
                        )

                    _raw_cc = cursor._get_shared_raw_cc()

                    saved = on_before_fork() if on_before_fork else None

                    child_pid = _real_fork()

                    if child_pid == 0:
                        _raw_cc().discard_watches()

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

            except ParseRequestError as err:
                _write_error(control_socket.write_response, err.request_id, err.code, err.message)

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

            except BaseException as err:
                import traceback
                tb = traceback.format_exc()
                try:
                    _write_error(
                        control_socket.write_response, request_id,
                        "internal_error", f"{type(err).__name__}: {err}\n{tb}",
                    )
                except Exception:
                    pass
                raise
    finally:
        control_socket.close()

def _resolve_callable(name: str):
    """Resolve a dotted name like '_thread.start_new_thread' to the actual object."""
    module_name, _, attr = name.rpartition(".")
    mod = __import__(module_name)
    for part in module_name.split(".")[1:]:
        mod = getattr(mod, part)
    return getattr(mod, attr)


def register_breakpoint_callback(breakpoint_dict, callback, log=None, disable_for=None):
    if "function" in breakpoint_dict:
        target = _resolve_callable(breakpoint_dict["function"])
        return install_function_breakpoint(target, callback, disable_for=disable_for)
    spec = BreakpointSpec(
        file=breakpoint_dict["file"],
        line=breakpoint_dict["line"],
        condition=breakpoint_dict.get("condition"),
    )
    return install_breakpoint(spec, callback, disable_for=disable_for, log=log)

def register_cursor_callback(cursor_dict, callback, on_missed=None):
    cursor.install_call_counter()
    target_f_lasti = cursor_dict.get("f_lasti")
    counts = tuple(cursor_dict["function_counts"])

    if target_f_lasti is None:
        cursor.watch(counts, on_start=callback, on_missed=on_missed)
        return

    os_tid = _thread.get_ident()

    def _on_counts_match():
        frame = _find_user_frame()
        target_code = frame.f_code if frame else None
        tool_id = _acquire_tool_id("retrace_cursor_advance")
        E = sys.monitoring.events

        def _on_instruction(code, offset):
            if _thread.get_ident() != os_tid:
                return
            if code is not target_code:
                return
            if offset != target_f_lasti:
                return
            sys.monitoring.set_events(tool_id, 0)
            sys.monitoring.register_callback(tool_id, E.INSTRUCTION, None)
            sys.monitoring.free_tool_id(tool_id)
            callback()

        sys.monitoring.register_callback(
            tool_id, E.INSTRUCTION,
            cursor.call_counter_disable_for(_on_instruction),
        )
        sys.monitoring.set_events(tool_id, E.INSTRUCTION)

    cursor.watch(counts,
                 on_start=cursor.call_counter_disable_for(_on_counts_match),
                 on_missed=on_missed)

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
        on_before_fork: Optional[Callable[[], Any]] = None,
        on_after_fork: Optional[Callable[[Any], None]] = None,
        disable_for: Optional[Callable] = None,
        get_thread_id: Optional[Callable[[], Any]] = None,
    ):
        self._get_thread_id = get_thread_id or _thread.get_ident
        self._disable_for = functional.sequence(cursor.call_counter_disable_for, disable_for)
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
            get_inspector=self._get_inspector,
            get_message_index=lambda: self._message_index,
            get_thread_id=self._get_thread_id,
            get_instruction_map=self._get_instruction_info,
            on_before_fork=on_before_fork,
            on_after_fork=on_after_fork,
        )

        try:
            intent = self._disable_for(lambda: next(self.event_loop))()
            self._handle_intent(intent)
        except StopIteration:
            self._done = True

    def _cursor_dict(self, snapshot: Optional[dict] = None) -> dict[str, Any]:
        """Build a cursor dict using the configured thread_id source.

        If *snapshot* is given (e.g. from ``cursor_snapshot().to_dict()``),
        overwrite its ``thread_id`` with the stable one.  Otherwise build
        a fresh cursor from current call counts.
        """
        if snapshot is not None:
            snapshot["thread_id"] = self._get_thread_id()
            if "lineno" not in snapshot:
                code = None
                if self._stopped_frame is not None:
                    code = self._stopped_frame.f_code
                elif self._last_code is not None:
                    code = self._last_code
                f_lasti = snapshot.get("f_lasti")
                if code is not None and f_lasti is not None:
                    snapshot["lineno"] = self._lineno_from_code(code, f_lasti)
            return snapshot
        return {
            "thread_id": self._get_thread_id(),
            "function_counts": list(cursor.current_call_counts()),
            "f_lasti": None,
        }

    def _set_backstop(self, message_index: int):
        self._backstop = message_index

    def _get_inspector(self) -> Optional[StoppedStateInspector]:
        if self._stopped_frame is not None:
            return FrameInspector(self._stopped_frame)
        return None

    def on_new_message(self, message):
        self._message_index += 1
        if self._done:
            return
        if self._backstop is not None and self._message_index >= self._backstop:
            err = BackstopHitError(
                message_index=self._message_index,
                cursor=self._cursor_dict(cursor.cursor_snapshot().to_dict()),
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
                log = self._disable_for(self._control_log) if os.getenv("RETRACE_CONTROL_LOG") == "1" else None
                self._monitor = register_breakpoint_callback(
                    intent.breakpoint, self._disable_for(self._on_breakpoint_hit),
                    log=log,
                    disable_for=self._disable_for,
                )

        elif isinstance(intent, StopAtCursor):
            register_cursor_callback(
                intent.cursor,
                self._disable_for(self._on_cursor_hit),
                on_missed=self._disable_for(lambda: self._send_reason("overshoot")),
            )

        elif isinstance(intent, RunToReturn):
            self._install_run_to_return(intent)

        elif isinstance(intent, NextInstruction):
            self._install_next_instruction()

        elif isinstance(intent, WaitForThreadChange):
            self._install_wait_for_thread_change()

        else:
            raise RuntimeError(f"unexpected intent: {intent}")

    def _install_run_to_return(self, intent: RunToReturn):
        counters = intent.function_counts
        cursor.install_call_counter()

        def _on_return():
            snapshot = self._cursor_dict(cursor.cursor_snapshot().to_dict())
            try:
                self.event_loop.send(snapshot)
                intent2 = self.event_loop.send("return")
                self._handle_intent(intent2)
            except StopIteration:
                self._cleanup()

        def _on_missed():
            self._send_reason("overshoot")

        cursor.watch(counters,
                     on_return=self._disable_for(_on_return),
                     on_missed=self._disable_for(_on_missed))

        if intent.max_call_counter is not None:
            limit_counters = counters + (intent.max_call_counter,)

            def _on_limit():
                self._send_reason("call_counter")

            cursor.watch(limit_counters, on_start=self._disable_for(_on_limit))

    def _install_next_instruction(self):
        if self._trace_monitor is not None:
            self._trace_monitor.close()

        os_tid = _thread.get_ident()
        tool_id = _acquire_tool_id("retrace_next_instr")
        E = sys.monitoring.events
        monitor = utils.InstructionMonitor(tool_id)
        self._trace_monitor = monitor

        def on_hit(code, offset):
            if _thread.get_ident() != os_tid:
                return
            monitor.close()
            self._trace_monitor = None
            self._last_code = code
            self._stopped_frame = _find_user_frame()
            cursor_dict = {
                "thread_id": self._get_thread_id(),
                "function_counts": list(cursor.current_call_counts()),
                "f_lasti": offset,
                "lineno": self._lineno_from_code(code, offset),
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

    def _install_wait_for_thread_change(self):
        def on_switch():
            cursor.set_on_thread_switch(None)
            self._stopped_frame = _find_user_frame()
            cursor_dict = self._cursor_dict(cursor.cursor_snapshot().to_dict())
            try:
                next_intent = self.event_loop.send(cursor_dict)
                self._handle_intent(next_intent)
            except StopIteration:
                self._cleanup()

        cursor.set_on_thread_switch(self._disable_for(on_switch))

    def _get_instruction_info(self) -> tuple[list[int], list[int]] | None:
        code = None
        if self._stopped_frame is not None:
            code = self._stopped_frame.f_code
        elif self._last_code is not None:
            code = self._last_code
        if code is None:
            return None
        return _build_instruction_info(code)

    @staticmethod
    def _lineno_from_code(code, offset):
        linenos, _ = _build_instruction_info(code)
        idx = offset // 2
        if idx < len(linenos):
            return linenos[idx]
        return 0

    def _on_instruction_hit(self, code, offset):
        self._last_code = code
        cursor_dict = {
            "thread_id": self._get_thread_id(),
            "function_counts": list(cursor.current_call_counts()),
            "f_lasti": offset,
            "lineno": self._lineno_from_code(code, offset),
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

    def _on_breakpoint_hit(self, frame):
        self._stopped_frame = _find_user_frame()
        try:
            intent = self.event_loop.send(self._cursor_dict(cursor.cursor_snapshot().to_dict()))
            self._handle_intent(intent)
        except StopIteration:
            self._cleanup()

    def _on_cursor_hit(self):
        self._stopped_frame = _find_user_frame()
        try:
            intent = self.event_loop.send(self._cursor_dict(cursor.cursor_snapshot().to_dict()))
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
        cursor.set_on_thread_switch(None)
        self._stopped_frame = None
