import os
import sys
from dataclasses import dataclass
from types import CodeType, FrameType
from typing import Callable, Optional

from . import install_call_counter, cursor_snapshot, call_counter_disable_for

@dataclass
class BreakpointSpec:
    file: str
    line: int
    condition: Optional[str] = None


@dataclass
class Breakpoint:
    code_predicate: Callable[[CodeType], bool]
    frame_predicate: Callable[[FrameType], bool]


class BreakpointMonitor:
    def __init__(self, tool_id: int, events_used: int):
        self._tool_id = tool_id
        self._events_used = events_used
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        E = sys.monitoring.events
        sys.monitoring.set_events(self._tool_id, 0)
        if self._events_used & E.PY_START:
            sys.monitoring.register_callback(self._tool_id, E.PY_START, None)
        if self._events_used & E.LINE:
            sys.monitoring.register_callback(self._tool_id, E.LINE, None)
        if self._events_used & E.CALL:
            sys.monitoring.register_callback(self._tool_id, E.CALL, None)
        try:
            sys.monitoring.free_tool_id(self._tool_id)
        except Exception:
            pass
        self._closed = True


def _acquire_tool_id(name: str) -> int:
    for tid in range(6):
        try:
            sys.monitoring.use_tool_id(tid, name)
            return tid
        except ValueError:
            continue
    raise RuntimeError("No free sys.monitoring tool IDs available")


def _compile_breakpoint(spec: BreakpointSpec) -> Breakpoint:
    target_file = os.path.realpath(spec.file)
    target_basename = os.path.basename(target_file)
    target_line = int(spec.line)
    cond_code = compile(spec.condition, "<breakpoint-condition>", "eval") if spec.condition else None

    def code_predicate(code: CodeType) -> bool:
        cf = code.co_filename
        if cf == target_file:
            return True
        # Pure string comparison only — os.path.realpath uses os.lstat
        # which is proxied during replay, causing a trace desync that
        # permanently kills sys.monitoring callbacks.
        cb = os.path.basename(cf)
        if cb != target_basename:
            return False
        return target_file.endswith(cf)

    def frame_predicate(frame: FrameType) -> bool:
        if frame.f_lineno != target_line:
            return False
        if cond_code is None:
            return True
        try:
            return bool(eval(cond_code, frame.f_globals, frame.f_locals))
        except Exception:
            return False

    return Breakpoint(code_predicate=code_predicate, frame_predicate=frame_predicate)


def install_breakpoint(
    breakpoint: BreakpointSpec,
    callback: Callable[[dict], None],
    log: Optional[Callable[[str], None]] = None,
    disable_for: Optional[Callable] = None,
) -> BreakpointMonitor:
    """Install a file:line breakpoint via sys.monitoring.

    Returns a monitor handle with `.close()` for teardown.
    ``log``, when provided, is called with diagnostic strings that the
    caller can forward over the control socket.
    ``disable_for``, when provided, wraps monitoring callbacks so they
    don't perturb call-count tracking.
    """
    _log = log or (lambda msg: None)
    _wrap = disable_for or call_counter_disable_for
    _log(f"install_breakpoint: file={breakpoint.file!r} line={breakpoint.line}")

    install_call_counter()
    compiled = _compile_breakpoint(breakpoint)
    tool_id = _acquire_tool_id("retrace_breakpoint")
    _log(f"tool_id={tool_id}")

    E = sys.monitoring.events
    DISABLE = sys.monitoring.DISABLE

    def on_py_start(code: CodeType, instruction_offset: int):  # noqa: ARG001
        if compiled.code_predicate(code):
            _log(f"PY_START match: {code.co_filename}:{code.co_name}")
            sys.monitoring.set_local_events(tool_id, code, E.LINE)
        return DISABLE

    def on_line(code: CodeType, line: int):  # noqa: ARG001
        frame = sys._getframe(1)
        if compiled.frame_predicate(frame):
            _log(f"LINE hit: {code.co_filename}:{line}")
            callback(cursor_snapshot().to_dict())
        return None

    events = E.PY_START | E.LINE
    sys.monitoring.register_callback(tool_id, E.PY_START, _wrap(on_py_start))
    sys.monitoring.register_callback(tool_id, E.LINE, _wrap(on_line))
    sys.monitoring.set_events(tool_id, E.PY_START)
    _log(f"hooks installed, monitoring PY_START events")

    return BreakpointMonitor(tool_id, events)


def install_function_breakpoint(
    target: object,
    callback: Callable[[dict], None],
    disable_for: Optional[Callable] = None,
) -> BreakpointMonitor:
    """Install a breakpoint on calls to a specific callable (including C functions).

    Uses sys.monitoring CALL events. The ``target`` is compared by identity
    against the callee of each call instruction.

    Returns a monitor handle with `.close()` for teardown.
    """
    _wrap = disable_for or call_counter_disable_for
    install_call_counter()
    tool_id = _acquire_tool_id("retrace_func_bp")

    E = sys.monitoring.events

    def on_call(code, offset, callee, arg0):  # noqa: ARG001
        if callee is target:
            callback(cursor_snapshot().to_dict())
        return None

    sys.monitoring.register_callback(tool_id, E.CALL, _wrap(on_call))
    sys.monitoring.set_events(tool_id, E.CALL)

    return BreakpointMonitor(tool_id, E.CALL)
