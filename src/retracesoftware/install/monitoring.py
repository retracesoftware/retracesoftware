"""sys.monitoring hooks for fine-grained divergence detection.

Uses Python 3.12+ monitoring hooks to checkpoint Python function calls
and returns inside the sandbox.  By default this uses ``sys.monitoring``;
callers that own a retrace ``CoordinateSpace`` can pass
``space.monitoring`` to register hooks on that space while the callback
runs in root space.  During recording, each event writes a compact
MONITOR message to the trace stream.  During replay, the same events
fire and verify the messages match.

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


def _disable_retrace_callback(callback):
    try:
        import retrace
    except ImportError:
        return callback

    disable = getattr(retrace, "disable", None) or getattr(retrace, "exclude", None)
    if disable is None:
        return callback
    return disable(callback)


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
    def install_monitoring(checkpoint_fn, level, *, disable_for=None, monitoring=None):
        raise RuntimeError(
            f"--monitor requires Python 3.12+ (have {sys.version})")
else:
    def _build_retrace_dirs(*, realpath=os.path.realpath, isdir=os.path.isdir):
        """Collect filesystem prefixes for all retracesoftware sub-packages."""
        dirs = set()
        import retracesoftware
        for p in getattr(retracesoftware, '__path__', []):
            dirs.add(realpath(p))
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
                        real = realpath(p)
                        # Use the directory containing the file/package.
                        d = real if isdir(real) else os.path.dirname(real)
                        dirs.add(d)
        return tuple(sorted(dirs))

    def _is_retrace_filename(filename, retrace_dirs, *, realpath=os.path.realpath):
        if filename.startswith("<frozen retrace"):
            return True
        if filename.startswith(retrace_dirs):
            return True
        if "retracesoftware" not in filename.split(os.sep):
            return False
        return realpath(filename).startswith(retrace_dirs)

    def _monitor_levels(monitoring):
        events = monitoring.events
        return {
            1: events.PY_START | events.PY_RETURN,
            2: (events.PY_START | events.PY_RETURN |
                events.CALL | events.C_RETURN |
                events.C_RAISE),
            3: (events.PY_START | events.PY_RETURN |
                events.CALL | events.C_RETURN |
                events.C_RAISE | events.LINE),
        }

    def install_monitoring(checkpoint_fn, level, *, disable_for=None, monitoring=None):
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
        if monitoring is None:
            monitoring = sys.monitoring
            wrap_callback = _disable_retrace_callback
        else:
            wrap_callback = lambda callback: callback

        monitor_levels = _monitor_levels(monitoring)
        if level not in monitor_levels:
            raise ValueError(f"monitor level must be 1–3, got {level}")

        tool_id = monitoring.PROFILER_ID
        disable_event = monitoring.DISABLE
        event_set = monitor_levels[level]
        event_names = monitoring.events
        realpath = os.path.realpath
        isdir = os.path.isdir
        if disable_for is not None:
            realpath = disable_for(realpath, unwrap_args=False)
            isdir = disable_for(isdir, unwrap_args=False)

        retrace_dirs = _build_retrace_dirs(realpath=realpath, isdir=isdir)

        def _is_retrace(filename):
            previous_count = begin_suppress_monitoring()
            try:
                return _is_retrace_filename(filename, retrace_dirs, realpath=realpath)
            finally:
                end_suppress_monitoring(previous_count)

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
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return disable_event
                checkpoint_fn('S:' + code.co_qualname)
            finally:
                _monitor_state.active = False

        def _py_return(code, instruction_offset, retval):
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return disable_event
                checkpoint_fn('R:' + code.co_qualname)
            finally:
                _monitor_state.active = False

        # ── CALL / C_RETURN / C_RAISE callbacks (level 2+) ───────

        def _call(code, instruction_offset, callable_obj, arg0):
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return
                checkpoint_fn('C:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        def _c_return(code, instruction_offset, callable_obj, arg0):
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return
                checkpoint_fn('CR:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        def _c_raise(code, instruction_offset, callable_obj, arg0):
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return
                checkpoint_fn('CX:' + _callable_name(callable_obj))
            finally:
                _monitor_state.active = False

        # ── LINE callback (level 3) ──────────────────────────────

        def _line(code, line_number):
            if _is_suppressed() or _is_active():
                return
            _monitor_state.active = True
            try:
                if _is_retrace(code.co_filename):
                    return disable_event
                checkpoint_fn('L:' + code.co_qualname + ':' + str(line_number))
            finally:
                _monitor_state.active = False

        # ── Register ──────────────────────────────────────────────

        monitoring.use_tool_id(tool_id, "retrace_monitor")

        if event_set & event_names.PY_START:
            monitoring.register_callback(
                tool_id, event_names.PY_START, wrap_callback(_py_start))
        if event_set & event_names.PY_RETURN:
            monitoring.register_callback(
                tool_id, event_names.PY_RETURN, wrap_callback(_py_return))
        if event_set & event_names.CALL:
            monitoring.register_callback(
                tool_id, event_names.CALL, wrap_callback(_call))
        if event_set & event_names.C_RETURN:
            monitoring.register_callback(
                tool_id, event_names.C_RETURN, wrap_callback(_c_return))
        if event_set & event_names.C_RAISE:
            monitoring.register_callback(
                tool_id, event_names.C_RAISE, wrap_callback(_c_raise))
        if event_set & event_names.LINE:
            monitoring.register_callback(
                tool_id, event_names.LINE, wrap_callback(_line))

        monitoring.set_events(tool_id, event_set)

        # ── Uninstall ─────────────────────────────────────────────

        def uninstall():
            monitoring.set_events(tool_id, 0)
            for evt in (event_names.PY_START,
                        event_names.PY_RETURN,
                        event_names.CALL,
                        event_names.C_RETURN,
                        event_names.C_RAISE,
                        event_names.LINE):
                monitoring.register_callback(tool_id, evt, None)
            monitoring.free_tool_id(tool_id)

        return uninstall
