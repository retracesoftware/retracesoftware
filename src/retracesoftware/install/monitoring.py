"""sys.monitoring hooks for fine-grained divergence detection.

Uses Python 3.12+ ``sys.monitoring`` to checkpoint Python function
calls and returns inside the sandbox.  During recording, each event
writes a compact MONITOR message to the trace stream.  During replay,
the same events fire and verify the messages match.

Granularity levels control which ``sys.monitoring`` events are enabled:

    0  — off (default, zero overhead)
    1  — PY_START + PY_RETURN
    2  — + CALL + C_RETURN + C_RAISE
    3  — + LINE

Each install function returns an **uninstall** callable, following the
same pattern as ``hooks.py``.

Usage::

    from retracesoftware.install.monitoring import install_monitoring

    uninstall = install_monitoring(checkpoint_fn, level=1)
    # ... run ...
    uninstall()
"""

import sys
import os
import _thread
from contextlib import contextmanager


_monitor_state = _thread._local()


def begin_suppress_monitoring():
    count = getattr(_monitor_state, "suppressed", 0)
    _monitor_state.suppressed = count + 1
    return count


def end_suppress_monitoring(previous_count):
    if previous_count:
        _monitor_state.suppressed = previous_count
    else:
        try:
            del _monitor_state.suppressed
        except AttributeError:
            pass


@contextmanager
def suppress_monitoring():
    count = begin_suppress_monitoring()
    try:
        yield
    finally:
        end_suppress_monitoring(count)

if sys.version_info < (3, 12):
    def install_monitoring(checkpoint_fn, level):
        raise RuntimeError(
            f"--monitor requires Python 3.12+ (have {sys.version})")
else:
    _TOOL_ID = sys.monitoring.PROFILER_ID

    MONITOR_LEVELS = {
        1: sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN,
        2: (sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN |
            sys.monitoring.events.CALL | sys.monitoring.events.C_RETURN |
            sys.monitoring.events.C_RAISE),
        3: (sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN |
            sys.monitoring.events.CALL | sys.monitoring.events.C_RETURN |
            sys.monitoring.events.C_RAISE | sys.monitoring.events.LINE),
    }

    def _build_retrace_dirs():
        """Collect filesystem prefixes for all retracesoftware sub-packages."""
        dirs = set()
        import retracesoftware
        for p in getattr(retracesoftware, '__path__', []):
            dirs.add(os.path.realpath(p))
        # Also cover editable-install src layouts where sub-packages
        # live in separate repos (stream, utils, functional, proxy, install).
        for name in list(sys.modules):
            if name.startswith('retracesoftware.'):
                mod = sys.modules[name]
                for attr in ('__path__', '__file__'):
                    val = getattr(mod, attr, None)
                    if val is None:
                        continue
                    paths = val if isinstance(val, list) else [val]
                    for p in paths:
                        real = os.path.realpath(p)
                        # Use the directory containing the file/package.
                        d = real if os.path.isdir(real) else os.path.dirname(real)
                        dirs.add(d)
        return tuple(sorted(dirs))

    def install_monitoring(checkpoint_fn, level):
        """Register ``sys.monitoring`` callbacks for divergence detection.

        Parameters
        ----------
        checkpoint_fn : callable(str)
            Called with a compact event string (e.g. ``"PY_START:foo"``).
            During recording this writes a MONITOR message; during replay
            it verifies against the next MONITOR message in the stream.
            Must already be a no-op outside the active sandbox/context.
        level : int
            Granularity level (1–3).  Level 0 should never reach here.

        Returns
        -------
        callable
            An uninstall function that tears down all monitoring state.
        """
        if level not in MONITOR_LEVELS:
            raise ValueError(f"monitor level must be 1–3, got {level}")

        events = MONITOR_LEVELS[level]
        retrace_dirs = _build_retrace_dirs()

        def _is_retrace(filename):
            return filename.startswith(retrace_dirs)

        def _is_suppressed():
            return getattr(_monitor_state, "suppressed", 0) > 0

        def _is_active():
            return getattr(_monitor_state, "active", False)

        def _callable_name(callable_obj):
            try:
                name = getattr(callable_obj, '__qualname__', None) or getattr(callable_obj, '__name__', None)
                if isinstance(name, str) and name:
                    return name
            except Exception:
                pass

            try:
                return object.__repr__(callable_obj)
            except Exception:
                return f'<{type(callable_obj).__name__}>'

        # ── PY_START / PY_RETURN callbacks ────────────────────────

        def _py_start(code, instruction_offset):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('S:' + code.co_qualname)
            finally:
                _monitor_state.active = False

        def _py_return(code, instruction_offset, retval):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('R:' + code.co_qualname)
            finally:
                _monitor_state.active = False

        # ── CALL / C_RETURN / C_RAISE callbacks (level 2+) ───────

        def _call(code, instruction_offset, callable_obj, arg0):
            if _is_retrace(code.co_filename):
                return
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('C:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        def _c_return(code, instruction_offset, callable_obj, arg0):
            if _is_retrace(code.co_filename):
                return
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('CR:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        def _c_raise(code, instruction_offset, callable_obj, arg0):
            if _is_retrace(code.co_filename):
                return
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('CX:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        # ── LINE callback (level 3) ──────────────────────────────

        def _line(code, line_number):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                checkpoint_fn('L:' + code.co_qualname + ':' + str(line_number))
            finally:
                _monitor_state.active = False

        # ── Register ──────────────────────────────────────────────

        sys.monitoring.use_tool_id(_TOOL_ID, "retrace_monitor")

        if events & sys.monitoring.events.PY_START:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.PY_START, _py_start)
        if events & sys.monitoring.events.PY_RETURN:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.PY_RETURN, _py_return)
        if events & sys.monitoring.events.CALL:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.CALL, _call)
        if events & sys.monitoring.events.C_RETURN:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.C_RETURN, _c_return)
        if events & sys.monitoring.events.C_RAISE:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.C_RAISE, _c_raise)
        if events & sys.monitoring.events.LINE:
            sys.monitoring.register_callback(
                _TOOL_ID, sys.monitoring.events.LINE, _line)

        sys.monitoring.set_events(_TOOL_ID, events)

        # ── Uninstall ─────────────────────────────────────────────

        def uninstall():
            sys.monitoring.set_events(_TOOL_ID, 0)
            for evt in (sys.monitoring.events.PY_START,
                        sys.monitoring.events.PY_RETURN,
                        sys.monitoring.events.CALL,
                        sys.monitoring.events.C_RETURN,
                        sys.monitoring.events.C_RAISE,
                        sys.monitoring.events.LINE):
                sys.monitoring.register_callback(_TOOL_ID, evt, None)
            sys.monitoring.free_tool_id(_TOOL_ID)

        return uninstall
