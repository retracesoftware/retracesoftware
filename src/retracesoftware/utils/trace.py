"""Instruction-level tracing for specific function invocations.

Provides ``trace_function_instructions`` which installs sys.monitoring
INSTRUCTION hooks on a single function invocation identified by
retracesoftware coordinates on the current thread, firing a callback for every
bytecode instruction until the function returns.
"""

import sys
import _thread
from types import CodeType

from .breakpoint import _acquire_tool_id


class TargetUnreachableError(Exception):
    """The target function invocation has already returned."""


def _retrace():
    import retrace

    return retrace


def _disable_retrace_callback(retrace, callback):
    disable = getattr(retrace, "disable", None) or retrace.exclude
    return disable(callback)


class InstructionMonitor:
    """Handle for an active trace_function_instructions session.

    Call ``.close()`` to tear down all monitoring hooks early.
    The monitor also auto-closes when the target function returns or unwinds.
    """

    def __init__(self, tool_id: int):
        self._tool_id = tool_id
        self._code: CodeType | None = None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        E = sys.monitoring.events
        if self._code is not None:
            try:
                sys.monitoring.set_local_events(self._tool_id, self._code, 0)
            except Exception:
                pass
        sys.monitoring.register_callback(self._tool_id, E.INSTRUCTION, None)
        sys.monitoring.register_callback(self._tool_id, E.PY_RETURN, None)
        sys.monitoring.register_callback(self._tool_id, E.PY_UNWIND, None)
        sys.monitoring.set_events(self._tool_id, 0)
        try:
            sys.monitoring.free_tool_id(self._tool_id)
        except Exception:
            pass


def trace_function_instructions(
    coordinates,
    callback,
    *,
    target_frame=None,
    thread_id=None,
    on_complete=None,
):
    """Fire *callback(code, instruction_offset)* for every bytecode instruction
    executed within the function invocation at *coordinates* on *thread_id*.
    When *thread_id* is omitted, the current thread is used.

    *on_complete* is called (with no arguments) after the monitor auto-closes
    due to the target function returning or unwinding.  It is **not** called
    when the caller manually invokes ``monitor.close()``.

    Returns an ``InstructionMonitor`` whose ``.close()`` tears down all hooks.
    The monitor auto-closes when the target function returns or unwinds.

    **Recursion caveat (v1):** INSTRUCTION events are per code-object, not per
    frame.  If the target function recurses, callbacks fire for all active
    invocations of the same code object.
    """
    retrace = _retrace()
    coordinates = tuple(coordinates)
    if thread_id is None:
        thread_id = _thread.get_ident()

    # --- Allocate a dedicated tool_id for INSTRUCTION events ---
    tool_id = _acquire_tool_id("retrace_trace_fn")
    monitor = InstructionMonitor(tool_id)
    E = sys.monitoring.events

    # ------------------------------------------------------------------
    # Phase 2 helpers — called once we have the target frame in hand
    # ------------------------------------------------------------------

    def _begin_tracing_with_frame(frame):
        """Enable per-instruction monitoring on *frame*'s code object."""
        if monitor._closed:
            return
        code = frame.f_code
        monitor._code = code

        def on_instruction(code_obj, offset):
            callback(code_obj, offset)
            return None

        sys.monitoring.register_callback(
            tool_id, E.INSTRUCTION,
            _disable_retrace_callback(retrace, on_instruction),
        )
        sys.monitoring.set_local_events(tool_id, code, E.INSTRUCTION)

        # Auto-teardown when target function exits
        def _on_exit(code_obj, *_args):
            if code_obj is not code:
                return None
            monitor.close()
            if on_complete is not None:
                on_complete()
            return None

        exit_cb = _disable_retrace_callback(retrace, _on_exit)
        sys.monitoring.register_callback(tool_id, E.PY_RETURN, exit_cb)
        sys.monitoring.register_callback(tool_id, E.PY_UNWIND, exit_cb)
        sys.monitoring.set_local_events(tool_id, code, E.INSTRUCTION)
        sys.monitoring.set_events(tool_id, E.PY_RETURN | E.PY_UNWIND)

    def _begin_tracing():
        """Resolve the target frame from the coordinate callback context."""
        _begin_tracing_with_frame(sys._getframe(1))

    begin = _disable_retrace_callback(retrace, _begin_tracing)

    if target_frame is not None:
        _begin_tracing_with_frame(target_frame)
    else:
        try:
            retrace.call_at(thread_id, coordinates, begin)
        except ValueError as exc:
            monitor.close()
            raise TargetUnreachableError(
                f"target {coordinates} already passed"
            ) from exc

    return monitor
