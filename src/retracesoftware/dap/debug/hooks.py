"""Debugger hooks with dual backend (sys.monitoring / sys.settrace).

Provides breakpoint, stepping, and pause support for the DAP adapter.
On Python 3.12+ uses sys.monitoring (per-code-object DISABLE, low overhead).
On older Python falls back to sys.settrace (per-frame skip, higher overhead).
"""

from __future__ import annotations

import os
import sys
import logging
from types import CodeType, FrameType
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..adapter import Adapter

log = logging.getLogger(__name__)

_has_monitoring = hasattr(sys, "monitoring")
_TOOL_ID = sys.monitoring.DEBUGGER_ID if _has_monitoring else 0

THREAD_ID = 1


class DebugHooks:
    """Installs debugger hooks and manages pause/resume/stepping state.

    Auto-selects sys.monitoring (3.12+) or sys.settrace (older).
    Retrace-internal code is skipped automatically.
    """

    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self._paused = False
        self._target_frame: FrameType | None = None
        self._stop_reason: str = ""

        self._step_mode: str | None = None  # 'next', 'step_in', 'step_out'
        self._step_depth: int = 0
        self._pause_requested = False
        self._seen_files: set[str] = set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def paused_frame(self) -> FrameType | None:
        return self._target_frame

    def resume(self) -> None:
        """Mark the target as resumed so the inline message loop exits."""
        self._paused = False
        self._target_frame = None
        self.adapter.inspector.invalidate()

    def request_pause(self) -> None:
        """Ask the target to pause at the next LINE event."""
        self._pause_requested = True

    def set_step_mode(self, mode: str | None, depth: int = 0) -> None:
        self._step_mode = mode
        self._step_depth = depth

    # -- install / uninstall ------------------------------------------------

    def install(self, disable_for: Callable) -> None:
        self._disable_for = disable_for
        if _has_monitoring:
            self._install_monitoring()
        else:
            self._install_settrace()

    def uninstall(self) -> None:
        if _has_monitoring:
            self._uninstall_monitoring()
        else:
            self._uninstall_settrace()

    # -- sys.monitoring backend (3.12+) ------------------------------------

    def _install_monitoring(self) -> None:
        try:
            sys.monitoring.use_tool_id(_TOOL_ID, "retrace")
        except ValueError:
            pass

        sys.monitoring.set_events(
            _TOOL_ID,
            sys.monitoring.events.LINE
            | sys.monitoring.events.PY_RETURN
            | sys.monitoring.events.RAISE,
        )
        sys.monitoring.register_callback(
            _TOOL_ID, sys.monitoring.events.LINE,
            self._disable_for(self._on_line))
        sys.monitoring.register_callback(
            _TOOL_ID, sys.monitoring.events.PY_RETURN,
            self._disable_for(self._on_return))
        sys.monitoring.register_callback(
            _TOOL_ID, sys.monitoring.events.RAISE,
            self._disable_for(self._on_raise))

    def _uninstall_monitoring(self) -> None:
        try:
            sys.monitoring.set_events(_TOOL_ID, 0)
            for evt in (sys.monitoring.events.LINE,
                        sys.monitoring.events.PY_RETURN,
                        sys.monitoring.events.RAISE):
                sys.monitoring.register_callback(_TOOL_ID, evt, None)
        except Exception:
            pass

    def _on_line(self, code: CodeType, line_number: int) -> object:
        filename = code.co_filename

        if _should_skip(filename):
            return sys.monitoring.DISABLE

        if filename not in self._seen_files:
            self._seen_files.add(filename)
            log.debug("_on_line new file: co_filename=%s", filename)

        self.adapter.cursor.advance(THREAD_ID, filename, line_number, code.co_name)
        self._check_stop_lazy(filename, line_number)
        return None

    def _on_return(
        self, code: CodeType, instruction_offset: int, retval: object,
    ) -> object:
        if self._step_mode == "step_out":
            frame = sys._getframe(1)
            depth = _frame_depth(frame)
            if depth <= self._step_depth - 1:
                self._step_mode = None
                self._pause_target(frame, "step")
        return None

    def _on_raise(
        self, code: CodeType, instruction_offset: int, exception: BaseException,
    ) -> object:
        if self.adapter.breakpoints.break_on_raised:
            frame = sys._getframe(1)
            self._pause_target(frame, "exception")
        return None

    # -- sys.settrace backend (< 3.12) ------------------------------------

    def _install_settrace(self) -> None:
        self._orig_trace = sys.gettrace()
        sys.settrace(self._wrap_trace(self._trace_dispatch))

    def _wrap_trace(self, func: Callable) -> Callable:
        disabled = self._disable_for(func)
        def wrapped(*args, **kwargs):
            result = disabled(*args, **kwargs)
            return self._wrap_trace(result) if callable(result) else result
        return wrapped

    def _uninstall_settrace(self) -> None:
        sys.settrace(self._orig_trace)

    def _trace_dispatch(self, frame: FrameType, event: str, arg: Any) -> Any:
        filename = frame.f_code.co_filename

        if _should_skip(filename):
            return None

        if event == "call":
            return self._trace_dispatch

        if event == "line":
            self.adapter.cursor.advance(
                THREAD_ID, filename, frame.f_lineno, frame.f_code.co_name)
            self._check_stop(frame, filename, frame.f_lineno)
            return self._trace_dispatch

        if event == "return":
            if self._step_mode == "step_out":
                depth = _frame_depth(frame)
                if depth <= self._step_depth - 1:
                    self._step_mode = None
                    self._pause_target(frame, "step")
            return self._trace_dispatch

        if event == "exception":
            if self.adapter.breakpoints.break_on_raised:
                self._pause_target(frame, "exception")
            return self._trace_dispatch

        return self._trace_dispatch

    # -- shared logic ------------------------------------------------------

    def _check_stop_lazy(self, filename: str, line_number: int) -> None:
        """Fast-path stop check for sys.monitoring — defers sys._getframe."""
        if self._pause_requested:
            self._pause_requested = False
            self._pause_target(sys._getframe(2), "pause")
            return

        if self.adapter.breakpoints.has_breakpoint(filename, line_number):
            log.debug("breakpoint hit: %s:%d", filename, line_number)
            frame = sys._getframe(2)
            bp = self.adapter.breakpoints.get_breakpoint(filename, line_number)
            if bp and bp.get("condition"):
                try:
                    if not eval(  # noqa: S307
                        bp["condition"], frame.f_globals, frame.f_locals,
                    ):
                        return
                except Exception:
                    pass
            self._pause_target(frame, "breakpoint")
            return

        if self._step_mode == "step_in":
            self._step_mode = None
            self._pause_target(sys._getframe(2), "step")
            return

        if self._step_mode == "next":
            frame = sys._getframe(2)
            depth = _frame_depth(frame)
            if depth <= self._step_depth:
                self._step_mode = None
                self._pause_target(frame, "step")

    def _check_stop(self, frame: FrameType, filename: str, line_number: int) -> None:
        """Check breakpoints and stepping conditions (settrace path)."""
        if self._pause_requested:
            self._pause_requested = False
            self._pause_target(frame, "pause")
            return

        if self.adapter.breakpoints.has_breakpoint(filename, line_number):
            log.debug("breakpoint hit: %s:%d", filename, line_number)
            bp = self.adapter.breakpoints.get_breakpoint(filename, line_number)
            if bp and bp.get("condition"):
                try:
                    if not eval(  # noqa: S307
                        bp["condition"], frame.f_globals, frame.f_locals,
                    ):
                        return
                except Exception:
                    pass
            self._pause_target(frame, "breakpoint")
            return

        if self._step_mode == "step_in":
            self._step_mode = None
            self._pause_target(frame, "step")
            return

        if self._step_mode == "next":
            depth = _frame_depth(frame)
            if depth <= self._step_depth:
                self._step_mode = None
                self._pause_target(frame, "step")

    def _pause_target(self, frame: FrameType, reason: str) -> None:
        """Send a stopped event and handle DAP messages until resumed."""
        self._target_frame = frame
        self._paused = True
        self._stop_reason = reason

        from ..protocol import types
        self.adapter.send(types.stopped_event(reason, thread_id=THREAD_ID))

        while self._paused:
            if not self.adapter.handle_one():
                break


_skip_cache: dict[str, bool] = {}

def _should_skip(filename: str) -> bool:
    """True for retrace-internal files that the debugger should ignore.

    Results are cached per filename string — there are very few unique
    co_filename values so the cache stays tiny.
    """
    r = _skip_cache.get(filename)
    if r is not None:
        return r
    parts = filename.replace(os.sep, "/").split("/")
    r = "retracesoftware" in parts
    _skip_cache[filename] = r
    return r


def _frame_depth(frame: FrameType) -> int:
    depth = 0
    f: FrameType | None = frame
    while f is not None:
        depth += 1
        f = f.f_back
    return depth
