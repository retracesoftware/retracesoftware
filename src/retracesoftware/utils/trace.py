"""Instruction-level tracing for specific function invocations.

Provides ``trace_function_instructions`` which installs sys.monitoring
INSTRUCTION hooks on a single function invocation identified by
(thread_id, call_counters), firing a callback for every bytecode
instruction until the function returns.
"""

import sys
import _thread
from types import CodeType

from . import (
    install_call_counter,
    current_call_counts,
    call_counter_disable_for,
    watch,
)
from .breakpoint import _acquire_tool_id


class TargetUnreachableError(Exception):
    """The target function invocation has already returned."""


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
        sys.monitoring.set_events(self._tool_id, 0)
        try:
            sys.monitoring.free_tool_id(self._tool_id)
        except Exception:
            pass


def _classify_position(target: tuple, current: tuple) -> str:
    """Classify *target* call_counters relative to *current*.

    Returns one of ``"behind"``, ``"exact"``, ``"ancestor"``, ``"ahead"``.

    - behind:   target already passed (first differing element is smaller)
    - exact:    target == current
    - ancestor: target is a strict prefix of current (we are deeper)
    - ahead:    target has not been reached yet
    """
    min_len = min(len(target), len(current))
    for i in range(min_len):
        if target[i] < current[i]:
            return "behind"
        if target[i] > current[i]:
            return "ahead"
    if len(target) == len(current):
        return "exact"
    if len(target) < len(current):
        return "ancestor"
    return "ahead"


def trace_function_instructions(
    thread_id, call_counters, callback, *, target_frame=None, on_complete=None
):
    """Fire *callback(code, instruction_offset)* for every bytecode instruction
    executed within the function invocation at (*thread_id*, *call_counters*).

    **Position cases** (determined automatically for same-thread callers):

    - *ahead*:    target not yet entered — arms ``watch(on_start)``
    - *ancestor*: currently deeper than target — arms ``watch(on_return)``
                  on the immediate child so we resume in the target frame
    - *exact*:    currently in the target — requires *target_frame* kwarg
    - *behind*:   target already returned — raises ``TargetUnreachableError``

    *on_complete* is called (with no arguments) after the monitor auto-closes
    due to the target function returning or unwinding.  It is **not** called
    when the caller manually invokes ``monitor.close()``.

    Returns an ``InstructionMonitor`` whose ``.close()`` tears down all hooks.
    The monitor auto-closes when the target function returns or unwinds.

    **Recursion caveat (v1):** INSTRUCTION events are per code-object, not per
    frame.  If the target function recurses, callbacks fire for all active
    invocations of the same code object.
    """
    install_call_counter()
    call_counters = tuple(call_counters)

    # --- Reachability check (same-thread only; cross-thread defers to watch) ---
    if _thread.get_ident() == thread_id:
        current = current_call_counts()
        position = _classify_position(call_counters, current)
        if position == "behind":
            raise TargetUnreachableError(
                f"target {call_counters} already passed (current: {current})"
            )
    else:
        current = None
        position = "ahead"

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
            tool_id, E.INSTRUCTION, call_counter_disable_for(on_instruction)
        )
        sys.monitoring.set_local_events(tool_id, code, E.INSTRUCTION)

        # Auto-teardown when target function exits
        def _on_exit():
            monitor.close()
            if on_complete is not None:
                on_complete()

        exit_cb = call_counter_disable_for(_on_exit)
        watch(thread_id, call_counters, on_return=exit_cb, on_unwind=exit_cb)

    def _begin_tracing():
        """Phase 1→2 bridge: resolve frame from the watch callback context."""
        _begin_tracing_with_frame(sys._getframe(1))

    begin = call_counter_disable_for(_begin_tracing)

    # ------------------------------------------------------------------
    # Phase 1 — navigate to the target based on classified position
    # ------------------------------------------------------------------

    if position == "exact":
        if target_frame is None:
            raise ValueError(
                "target_frame is required when current position matches call_counters"
            )
        _begin_tracing_with_frame(target_frame)

    elif position == "ancestor":
        # The immediate child of the target is at one level deeper than target.
        # When that child returns, execution resumes in the target frame.
        child_counters = current[:len(call_counters) + 1]
        watch(thread_id, child_counters, on_return=begin, on_unwind=begin)

    else:  # ahead
        watch(thread_id, call_counters, on_start=begin)

    return monitor
