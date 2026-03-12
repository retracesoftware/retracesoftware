"""retracesoftware.cursor - Call-count tracking and cursor positioning.

Standalone module for cursor / CallCounter functionality.
Imports the cursor C extension directly; no dependency on retracesoftware.utils.
"""
import os
import sys
import _thread
from dataclasses import dataclass

_DEBUG_MODE = os.getenv("RETRACE_DEBUG", "").strip().lower() in {
    "1", "true", "yes", "y", "on",
}

try:
    if _DEBUG_MODE:
        import _retracesoftware_cursor_debug as _cursor_mod  # type: ignore
    else:
        import _retracesoftware_cursor_release as _cursor_mod  # type: ignore
except Exception:
    raise ImportError("Failed to load retracesoftware cursor native extension")

_RawCallCounter = _cursor_mod.CallCounter

_shared_raw_cc = _RawCallCounter()

def _get_shared_raw_cc():
    return _shared_raw_cc


_cc_tool_id = None


class CallCounter:
    """Process-wide call-count tracker.

    Owns the ``sys.monitoring`` hooks and provides ``disable_for``.
    Call to get a thread-scoped ThreadCallCounts context::

        cc = CallCounter()
        tc = cc()              # ThreadCallCounts for current thread
        tc.add_watch(counts, on_return=cc.disable_for(cb))
        with tc:
            target()
    """

    def __init__(self):
        raw_cc = _get_shared_raw_cc()
        object.__setattr__(self, "_cc", raw_cc)
        # Bind hot-path native methods directly so attribute access
        # does not go through Python interception.
        object.__setattr__(self, "current", raw_cc.current)
        object.__setattr__(self, "frame_positions", raw_cc.frame_positions)
        object.__setattr__(self, "disable_for", raw_cc.disable_for)

    @property
    def installed(self):
        return self._cc.installed

    @property
    def tool_id(self):
        return self._cc.tool_id

    @property
    def depth(self):
        return self._cc.depth

    @property
    def on_thread_switch(self):
        return self._cc.on_thread_switch

    @on_thread_switch.setter
    def on_thread_switch(self, value):
        self._cc.on_thread_switch = value

    def install(self):
        """Install call-count tracking hooks via sys.monitoring (3.12+).

        Newly created threads are tracked via thread-local ThreadCallCounts
        in the native layer; no Python thread middleware is required here.
        """
        global _cc_tool_id
        if _cc_tool_id is not None:
            return
        self._cc.install()
        _cc_tool_id = self._cc.tool_id

    def uninstall(self):
        """Remove call-count tracking hooks and reset state."""
        global _cc_tool_id
        if _cc_tool_id is None:
            return
        self._cc.uninstall()
        _cc_tool_id = None

    def position(self):
        return self().position()

    def _yield_at_impl(self, callback, thread_id, call_counts):
        if thread_id == _thread.get_ident():
            self._cc.yield_at(callback, call_counts)
            return

        def _arm_on_target_thread():
            callback_on_thread(thread_id, callback)

        self._cc.yield_at(_arm_on_target_thread, call_counts)

    def yield_at(self, callback, thread_id, call_counts):
        wrapped = getattr(self, "_yield_at_wrapped", None)
        if wrapped is None:
            wrapped = self._cc.disable_for(self._yield_at_impl)
            object.__setattr__(self, "_yield_at_wrapped", wrapped)
        wrapped(callback, thread_id, call_counts)

    def __call__(self):
        """Return the ThreadCallCounts for the current thread.

        The returned object supports the context manager protocol
        and exposes current(), frame_positions(), add_watch(), etc.
        """
        if not self.installed:
            self.install()
        return self._cc()

    def __len__(self):
        return self.depth

    def __enter__(self):
        self.install()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.uninstall()
        return False

    def __repr__(self):
        return repr(self._cc)


def callback_on_thread(thread_id, callback):
    """Schedule *callback* to fire on the next monitored event on *thread_id*.

    Piggybacks on the existing PY_START / PY_RETURN / PY_UNWIND / JUMP
    handlers, so the callback fires as soon as the target thread enters,
    exits, or jumps in any Python function.
    """
    _get_shared_raw_cc().callback_on_thread(thread_id, callback)


# ---------------------------------------------------------------------------
# Shared Python-level CallCounter singleton (used by legacy functions)
# ---------------------------------------------------------------------------

_shared_cc = None

def _get_shared_cc():
    global _shared_cc
    if _shared_cc is None:
        _shared_cc = CallCounter()
    return _shared_cc


# ---------------------------------------------------------------------------
# Legacy module-level functions (delegate to shared CallCounter)
# ---------------------------------------------------------------------------

def _get_default_call_counter():
    return _get_shared_raw_cc()

def install_call_counter():
    """Install per-thread call-count tracking hooks."""
    _get_shared_cc().install()

def uninstall_call_counter():
    """Remove call-count tracking hooks and reset the stack."""
    _get_shared_cc().uninstall()

current_call_counts = _shared_raw_cc.current
call_counter_frame_positions = _shared_raw_cc.frame_positions

def call_counter_position():
    """Return (call_count, f_lasti) pairs for every frame on the stack."""
    tc = _get_default_call_counter()()
    return tc.position()

def yield_at_call_counts(callback, call_counts):
    """Arm a one-shot callback for a target call-counts position on the current thread."""
    _get_shared_cc().yield_at(callback, _thread.get_ident(), call_counts)

call_counter_disable_for = _shared_raw_cc.disable_for

def set_on_thread_switch(callback):
    """Set the global on_thread_switch callback on the shared CallCounter."""
    _get_shared_raw_cc().on_thread_switch = callback

def add_watch(call_counts, *, on_start=None, on_return=None,
              on_unwind=None, on_backjump=None, on_missed=None):
    """Arm one-shot callbacks on the current thread for a target call-counts position."""
    tc = _get_default_call_counter()()
    tc.add_watch(
        call_counts,
        on_start=on_start, on_return=on_return,
        on_unwind=on_unwind, on_backjump=on_backjump,
        on_overshoot=on_missed,
    )

watch = add_watch


# ---------------------------------------------------------------------------
# Cursor -- immutable data type representing a position in a recorded trace
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cursor:
    """A position in a recorded trace.

    ``thread_id``: the OS thread that was executing.
    ``function_counts``: per-frame call counts from root to leaf.
    ``f_lasti``: bytecode offset of the top frame, or None for function entry.
    """
    thread_id: int
    function_counts: tuple
    f_lasti: int | None = None

    def to_dict(self) -> dict:
        d: dict = {"thread_id": self.thread_id, "function_counts": list(self.function_counts)}
        if self.f_lasti is not None:
            d["f_lasti"] = self.f_lasti
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Cursor":
        return cls(
            thread_id=d["thread_id"],
            function_counts=tuple(d["function_counts"]),
            f_lasti=d.get("f_lasti"),
        )

def cursor_snapshot() -> Cursor:
    """Take a snapshot of the current execution position as a Cursor."""
    counts = current_call_counts()
    positions = call_counter_frame_positions()
    return Cursor(
        thread_id=_thread.get_ident(),
        function_counts=counts,
        f_lasti=positions[-1] if positions else None,
    )


def _yield_at_cursor_impl(thread_id, counters, f_lasti, callback):
    raw_cc = _get_default_call_counter()
    py_cc = _get_shared_cc()
    tc = raw_cc()

    if f_lasti is None:
        tc.add_watch(counters, on_start=callback)
        return

    def _phase2():
        frame = sys._getframe(1)
        code = frame.f_code
        tool_id = py_cc.tool_id
        target = f_lasti

        if frame.f_lasti >= target:
            callback()
            return

        def _on_instruction(code_obj, offset):
            if offset == target:
                sys.monitoring.set_local_events(tool_id, code, 0)
                callback()
            return sys.monitoring.DISABLE

        sys.monitoring.register_callback(
            tool_id, sys.monitoring.events.INSTRUCTION,
            raw_cc.disable_for(_on_instruction),
        )
        sys.monitoring.set_local_events(
            tool_id, code, sys.monitoring.events.INSTRUCTION,
        )

    wrapped = raw_cc.disable_for(_phase2)
    tc.add_watch(counters, on_return=wrapped, on_unwind=wrapped)


_yield_at_cursor_wrapped = None

def yield_at_cursor(*args):
    """Yield at a precise cursor position using a two-phase approach.

    If *f_lasti* is ``None``, arms *callback* via ``on_start`` for
    *counters* directly (the function-entry case).

    Otherwise uses *counters* to identify a child function invocation and
    *f_lasti* as the bytecode offset in the **parent frame** where we
    need to stop:

    Phase 1 -- ``watch(on_return/on_unwind)`` waits for the child at
    *counters* to finish so the parent frame becomes active.

    Phase 2 -- enables per-code-object ``INSTRUCTION`` monitoring on the
    parent frame and fires *callback* when ``instruction_offset == f_lasti``.

    The function itself is wrapped with ``disable_for`` so it does not
    perturb the call-count stack.
    """
    if len(args) == 3:
        callback, thread_id, cursor = args
        if isinstance(cursor, Cursor):
            counters = cursor.function_counts
            f_lasti = cursor.f_lasti
        else:
            counters = cursor
            f_lasti = None
    elif len(args) == 4:
        thread_id, counters, f_lasti, callback = args
    else:
        raise TypeError("yield_at_cursor expects (callback, thread_id, cursor) or (thread_id, counters, f_lasti, callback)")

    global _yield_at_cursor_wrapped
    if _yield_at_cursor_wrapped is None:
        _yield_at_cursor_wrapped = call_counter_disable_for(_yield_at_cursor_impl)
    _yield_at_cursor_wrapped(thread_id, counters, f_lasti, callback)


# Backward-compat aliases
install_cursor_hooks = install_call_counter
uninstall_cursor_hooks = uninstall_call_counter
current_cursor = current_call_counts
cursor_frame_positions = call_counter_frame_positions
cursor_position = call_counter_position
cursor_disable_for = call_counter_disable_for
