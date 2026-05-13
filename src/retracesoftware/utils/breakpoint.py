import os
import sys
import threading
import _thread
from dataclasses import dataclass
from types import CodeType, FrameType
from typing import Callable, Optional

_HAS_MONITORING = hasattr(sys, "monitoring")

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
        if not _HAS_MONITORING:
            self._closed = True
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


class TraceBreakpointMonitor:
    def __init__(
        self,
        dispatch: Callable[[FrameType, str, object], bool],
        disable_for: Callable,
    ) -> None:
        self._dispatch = dispatch
        self._orig_trace = sys.gettrace()
        self._orig_threading_trace = threading.gettrace()
        self._closed = False
        self._replacement_trace = None
        self._trace = disable_for(self._trace_impl)
        sys.settrace(self._trace)
        threading.settrace(self._trace)

    @property
    def trace_function(self):
        return self._trace

    @property
    def original_trace_function(self):
        return self._orig_trace

    def _trace_impl(self, frame: FrameType, event: str, arg: object):
        if self._closed:
            if self._replacement_trace is not None:
                return self._replacement_trace
            replacement = frame.f_trace
            if replacement is not None and replacement is not self._trace:
                return replacement
            return self._orig_trace
        orig_next = None
        if self._orig_trace is not None and self._orig_trace is not self._trace:
            orig_next = self._orig_trace(frame, event, arg)
        keep_tracing_frame = self._dispatch(frame, event, arg)
        if self._closed:
            if self._replacement_trace is not None:
                return self._replacement_trace
            replacement = frame.f_trace
            if replacement is not None and replacement is not self._trace:
                return replacement
            return orig_next
        if event == "call" and not keep_tracing_frame:
            return orig_next
        return self._trace

    def close(self, replacement_trace=None) -> None:
        if self._closed:
            return
        self._replacement_trace = replacement_trace
        self._closed = True
        if replacement_trace is None:
            sys.settrace(self._orig_trace)
        threading.settrace(self._orig_threading_trace)


def _acquire_tool_id(name: str) -> int:
    for tid in range(6):
        try:
            sys.monitoring.use_tool_id(tid, name)
            return tid
        except ValueError:
            continue
    raise RuntimeError("No free sys.monitoring tool IDs available")


def _retrace():
    import retrace

    return retrace


def _callback_wrapper(retrace, disable_for):
    retrace_disabled = getattr(retrace, "disable", None) or retrace.exclude
    if disable_for is None:
        return retrace_disabled

    def wrap(callback):
        return disable_for(retrace_disabled(callback))

    return wrap


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


def _find_monitored_frame(code: CodeType) -> Optional[FrameType]:
    """Return the live frame for a sys.monitoring callback's code object."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code is code:
            return frame
        frame = frame.f_back
    return None


def _coordinate_snapshot(frame: Optional[FrameType] = None) -> dict:
    retrace = _retrace()
    snapshot = {
        "thread_id": _thread.get_ident(),
        "coordinates": list(retrace.coordinates()),
    }
    if frame is not None:
        snapshot["f_lasti"] = frame.f_lasti
        snapshot["lineno"] = frame.f_lineno
    return snapshot


def install_breakpoint(
    breakpoint: BreakpointSpec,
    callback: Callable[[dict], None],
    log: Optional[Callable[[str], None]] = None,
    disable_for: Optional[Callable] = None,
) -> BreakpointMonitor | TraceBreakpointMonitor:
    """Install a file:line breakpoint.

    Returns a monitor handle with `.close()` for teardown.
    ``log``, when provided, is called with diagnostic strings that the
    caller can forward over the control socket.
    ``disable_for``, when provided, wraps monitoring callbacks so they
    don't perturb retrace-python coordinates.
    """
    retrace = _retrace()
    _log = log or (lambda msg: None)
    _wrap = _callback_wrapper(retrace, disable_for)
    _log(f"install_breakpoint: file={breakpoint.file!r} line={breakpoint.line}")

    compiled = _compile_breakpoint(breakpoint)

    if not _HAS_MONITORING:
        def on_trace(frame: FrameType, event: str, arg: object):  # noqa: ARG001
            if not compiled.code_predicate(frame.f_code):
                return False
            frame.f_trace_lines = True
            frame.f_trace_opcodes = True
            if event == "line" and compiled.frame_predicate(frame):
                _log(f"LINE hit: {frame.f_code.co_filename}:{frame.f_lineno}")
                callback(_coordinate_snapshot(frame))
            return True

        _log("hooks installed, using sys.settrace fallback")
        return TraceBreakpointMonitor(on_trace, _wrap)

    tool_id = _acquire_tool_id("retrace_breakpoint")
    _log(f"tool_id={tool_id}")

    E = sys.monitoring.events
    DISABLE = sys.monitoring.DISABLE

    def on_py_start(code: CodeType, instruction_offset: int):  # noqa: ARG001
        if compiled.code_predicate(code):
            _log(f"PY_START match: {code.co_filename}:{code.co_name}")
            sys.monitoring.set_local_events(tool_id, code, E.LINE)
        return DISABLE

    def on_line(code: CodeType, line: int):
        frame = _find_monitored_frame(code)
        if frame is not None and compiled.frame_predicate(frame):
            _log(f"LINE hit: {code.co_filename}:{line}")
            callback(_coordinate_snapshot(frame))
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
) -> BreakpointMonitor | TraceBreakpointMonitor:
    """Install a breakpoint on calls to a specific callable (including C functions).

    Uses sys.monitoring CALL events. The ``target`` is compared by identity
    against the callee of each call instruction.

    Returns a monitor handle with `.close()` for teardown.
    """
    retrace = _retrace()
    _wrap = _callback_wrapper(retrace, disable_for)

    if not _HAS_MONITORING:
        target_code = getattr(target, "__code__", None)
        if target_code is None:
            raise RuntimeError("function breakpoints on non-Python callables require Python 3.12+")

        def on_trace(frame: FrameType, event: str, arg: object):  # noqa: ARG001
            if frame.f_code is not target_code:
                return False
            if event == "call":
                callback(_coordinate_snapshot(frame))
            return True

        return TraceBreakpointMonitor(on_trace, _wrap)

    tool_id = _acquire_tool_id("retrace_func_bp")

    E = sys.monitoring.events

    def on_call(code, offset, callee, arg0):  # noqa: ARG001
        if callee is target:
            callback(_coordinate_snapshot(_find_monitored_frame(code)))
        return None

    sys.monitoring.register_callback(tool_id, E.CALL, _wrap(on_call))
    sys.monitoring.set_events(tool_id, E.CALL)

    return BreakpointMonitor(tool_id, E.CALL)
