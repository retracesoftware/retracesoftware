"""sys.monitoring hooks for fine-grained divergence detection.

Uses Python 3.12+ ``sys.monitoring`` to checkpoint Python function
calls and returns inside the sandbox.  During recording, each event
writes a compact MONITOR message to the trace stream.  During replay,
the same events fire and verify the messages match.

Granularity levels control which ``sys.monitoring`` events are enabled:

    0  — off (default, zero overhead)
    1  — PY_START + PY_RETURN
    2  — + CALL + C_RETURN
    3  — + LINE

Each install function returns an **uninstall** callable, following the
same pattern as ``hooks.py``.

Usage::

    from retracesoftware.install.monitoring import install_monitoring

    uninstall = install_monitoring(system, checkpoint_fn, level=1)
    # ... run ...
    uninstall()
"""

import sys
import os
import _thread

if sys.version_info < (3, 12):
    def install_monitoring(system, checkpoint_fn, level):
        raise RuntimeError(
            f"--monitor requires Python 3.12+ (have {sys.version})")
else:
    _TOOL_ID = sys.monitoring.PROFILER_ID

    MONITOR_LEVELS = {
        1: sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN,
        2: (sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN |
            sys.monitoring.events.CALL | sys.monitoring.events.C_RETURN),
        3: (sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN |
            sys.monitoring.events.CALL | sys.monitoring.events.C_RETURN |
            sys.monitoring.events.LINE),
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

    def install_monitoring(system, checkpoint_fn, level):
        """Register ``sys.monitoring`` callbacks for divergence detection.

        Parameters
        ----------
        system : System
            The proxy system — ``system._in_sandbox`` is used to guard
            callbacks so they only fire inside a record/replay context.
        checkpoint_fn : callable(str)
            Called with a compact event string (e.g. ``"PY_START:foo"``).
            During recording this writes a MONITOR message; during replay
            it verifies against the next MONITOR message in the stream.
            Must already be wrapped with ``system.disable_for`` by the
            caller.
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
        in_sandbox = system._in_sandbox

        # Thread-local reentrancy guard.
        _guard = _thread._local()

        def _is_retrace(filename):
            return filename.startswith(retrace_dirs)

        # ── PY_START / PY_RETURN callbacks ────────────────────────

        def _py_start(code, instruction_offset):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if getattr(_guard, 'active', False):
                return
            if not in_sandbox():
                return
            _guard.active = True
            try:
                checkpoint_fn('S:' + code.co_qualname)
            finally:
                _guard.active = False

        def _py_return(code, instruction_offset, retval):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if getattr(_guard, 'active', False):
                return
            if not in_sandbox():
                return
            _guard.active = True
            try:
                checkpoint_fn('R:' + code.co_qualname)
            finally:
                _guard.active = False

        # ── CALL / C_RETURN callbacks (level 2+) ─────────────────

        def _call(code, instruction_offset, callable_obj, arg0):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if getattr(_guard, 'active', False):
                return
            if not in_sandbox():
                return
            _guard.active = True
            try:
                qn = getattr(callable_obj, '__qualname__',
                             getattr(callable_obj, '__name__', repr(callable_obj)))
                checkpoint_fn('C:' + qn)
            finally:
                _guard.active = False

        def _c_return(code, instruction_offset, callable_obj, arg0):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if getattr(_guard, 'active', False):
                return
            if not in_sandbox():
                return
            _guard.active = True
            try:
                qn = getattr(callable_obj, '__qualname__',
                             getattr(callable_obj, '__name__', repr(callable_obj)))
                checkpoint_fn('CR:' + qn)
            finally:
                _guard.active = False

        # ── LINE callback (level 3) ──────────────────────────────

        def _line(code, line_number):
            if _is_retrace(code.co_filename):
                return sys.monitoring.DISABLE
            if getattr(_guard, 'active', False):
                return
            if not in_sandbox():
                return
            _guard.active = True
            try:
                checkpoint_fn('L:' + code.co_qualname + ':' + str(line_number))
            finally:
                _guard.active = False

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
                        sys.monitoring.events.LINE):
                sys.monitoring.register_callback(_TOOL_ID, evt, None)
            sys.monitoring.free_tool_id(_TOOL_ID)

        return uninstall
