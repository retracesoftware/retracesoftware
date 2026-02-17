"""Install helpers for the retrace system.

Provides:

- System install functions (``install_system``, ``run_with_context``)
  for bootstrapping the retrace hook machinery.
- ``install_for_pytest`` for in-process testing with a ``TestRunner``
  that records then replays a function call, raising on divergence.
"""

import pkgutil
import importlib


# ── Thread state labels ───────────────────────────────────────

thread_states = [
    "disabled",  # Default state when retrace is disabled for a thread
    "internal",  # Default state when retrace is disabled for a thread
    "external",  # When target thread is running outside the python system to be recorded
    "retrace",   # When target thread is running outside the retrace system
    "importing", # When target thread is running outside the retrace system
    "gc",        # When the target thread is running inside the python garbage collector
]


# ── ImmutableTypes ────────────────────────────────────────────

class ImmutableTypes(set):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __contains__(self, item):
        assert isinstance(item, type)

        if super().__contains__(item):
            return True

        for elem in self:
            if issubclass(item, elem):
                self.add(item)
                return True

        return False


# ── Private helpers (old install path) ────────────────────────

def _patch_import(thread_state, patcher, sync, checkpoint):
    import builtins
    import _imp
    import importlib._bootstrap_external as _bootstrap_external
    import retracesoftware.functional as functional
    import retracesoftware.utils as utils

    builtins.__import__ = thread_state.dispatch(builtins.__import__, internal = thread_state.wrap('importing', builtins.__import__))

    def exec(source, globals = None, locals = None):
        checkpoint(f"exec module: {globals.get('__name__', 'unknown')}")
        res = builtins.exec(source, globals, locals)
        patcher(globals, False)
        return res

    def patch(module):
        patcher(module.__dict__, False)
        return module

    _imp.exec_dynamic = thread_state.dispatch(_imp.exec_dynamic, 
        importing = functional.juxt(
                        thread_state.wrap('internal', _imp.exec_dynamic), 
                        patch))

    _imp.exec_builtin = thread_state.dispatch(_imp.exec_builtin, 
        importing = functional.juxt(
                        thread_state.wrap('internal', _imp.exec_builtin), 
                        patch))

    utils.update(_bootstrap_external._LoaderBasics, "exec_module", 
                 utils.wrap_func_with_overrides,
                 exec = thread_state.dispatch(builtins.exec, importing = thread_state.wrap('internal', sync(exec))))


def _wrapped_weakref(ref, thread_state, wrap_callback):
    orig_new = ref.__new__

    def __new__(cls, ob, callback=None, **kwargs):
        return orig_new(cls, ob, wrap_callback(callback) if callback else None)
    
    return type('ref', (ref, ), {'__new__': thread_state.dispatch(orig_new, internal = __new__)})


def _patch_weakref(thread_state, wrap_callback):
    import _weakref
    from retracesoftware.install.replace import update

    update(_weakref.ref, _wrapped_weakref(_weakref.ref, thread_state, wrap_callback))


def _init_weakref_legacy():
    import weakref
    def dummy_callback():
        pass

    class DummyTarget:
        pass

    f = weakref.finalize(DummyTarget(), dummy_callback)
    f.detach()


# ── System install (old code path) ───────────────────────────

def install_system(system):
    """Install all module patches and system hooks.

    Used by ``__main__.py``'s ``record()`` and ``replay()`` functions
    (the old thread-state-dispatch code path).
    """
    import sys
    import threading
    import retracesoftware.functional as functional
    import retracesoftware.utils as utils
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.patcher import patch_module, create_patcher, patch_imported_module
    from retracesoftware.install.startthread import patch_thread_start

    _patch_weakref(thread_state = system.thread_state, wrap_callback = system.wrap_weakref_callback)

    _init_weakref_legacy()
    
    preload = pkgutil.get_data("retracesoftware", "preload.txt")

    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    for function in utils.stack_functions():
        system.exclude_from_stacktrace(function)

    def recursive_disable(func):
        if not callable(func):
            return func
        
        def wrapped(*args, **kwargs):
            with system.thread_state.select('disabled'):
                return recursive_disable(func(*args, **kwargs))
        
        return wrapped

    sys.settrace = functional.sequence(recursive_disable, sys.settrace)
    sys.setprofile = functional.sequence(recursive_disable, sys.setprofile)
    threading.settrace = functional.sequence(recursive_disable, threading.settrace)

    sys.settrace(sys.gettrace())
    sys.setprofile(sys.getprofile())

    system.checkpoint('About to install retrace system')

    module_config = ModuleConfigResolver()

    patch_loaded = functional.partial(patch_module, create_patcher(system), module_config)
    patch_imported = functional.partial(patch_imported_module, create_patcher(system), system.checkpoint, module_config)
    
    system.checkpoint('Started installing system 1')
    
    for modname in module_config.keys():
        if modname in sys.modules:
            patch_loaded(sys.modules[modname].__dict__, True)

    system.checkpoint('About to patch threading')

    patch_thread_start(system.thread_state)
    threading.current_thread().__retrace__ = system

    system.checkpoint('About to patch import')
    _patch_import(thread_state = system.thread_state, 
                 patcher = patch_imported, 
                 sync = system.sync, 
                 checkpoint = system.checkpoint)

    importlib.import_module = \
        system.thread_state.dispatch(system.disable_for(importlib.import_module),
                              internal = system.thread_state.wrap('importing', importlib.import_module))

    system.checkpoint('About to patch preload libraries')

    system.checkpoint('system patched...')


# ── System install + run (new gate-based path) ───────────────

def run_with_context(system, context, argv, wrap_callback, trace_shutdown=False):
    """Run a Python command inside a System context (record or replay).

    Parameters
    ----------
    system : System
        The proxy system (new gate-based System from proxy/system.py).
    context : context manager
        A ``system.record_context(writer)`` or
        ``system.replay_context(reader)`` — any context manager that
        activates the system's gates for the duration.  Must be
        reusable (child threads enter the same context).
    argv : list[str]
        Command-line arguments: ``['-m', 'module', ...]`` or
        ``['script.py', ...]``.
    wrap_callback : callable(callback) → callback
        Wraps a weakref callback for recording/replaying.  Built by
        the caller using ``utils.observer`` and the appropriate
        adapter hooks (``on_weakref_callback_start/end``).
    trace_shutdown : bool
        If True, atexit hooks run inside the context so their I/O
        is recorded/replayed.  If False, atexit hooks run after
        the context exits.
    """
    import sys
    import atexit
    import retracesoftware.functional as functional
    from retracesoftware.run import run_python_command, wait_for_non_daemon_threads
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.patcher import patch, install_hash_patching
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded
    from retracesoftware.install.hooks import install_trace_hooks, install_weakref_hooks, init_weakref
    from retracesoftware.install.startthread import patch_thread_start

    uninstallers = []

    # ── one-time system setup ─────────────────────────────────
    uninstallers.append(install_hash_patching(system))
    uninstallers.append(install_trace_hooks(system.disable_for))
    uninstallers.append(install_weakref_hooks(system, wrap_callback))
    init_weakref()

    # ── thread patching ───────────────────────────────────────
    def wrapper(fn):
        def wrapped(*a, **kw):
            with context:
                return fn(*a, **kw)
        return wrapped

    dispatch = system.create_dispatch(
        external=wrapper,
        internal=functional.identity,
        disabled=functional.identity)

    uninstallers.append(patch_thread_start(dispatch))

    # ── preload commonly-used modules before patching ─────────
    preload = pkgutil.get_data("retracesoftware", "preload.txt")
    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    # ── build the module patcher ──────────────────────────────
    module_config = ModuleConfigResolver()
    patch_undos = []

    def module_patcher(namespace, update_refs):
        name = namespace.get('__name__')
        if name and name in module_config:
            undo = patch(namespace, module_config[name], system, update_refs)
            patch_undos.append(undo)

    # ── patch already-loaded modules, install hooks, run ──────
    patch_already_loaded(module_patcher, module_config)
    uninstallers.append(install_import_hooks(system.disable_for, module_patcher))

    try:
        with context:
            try:
                run_python_command(argv)
            finally:
                wait_for_non_daemon_threads()
                if trace_shutdown:
                    try:
                        atexit._run_exitfuncs()
                    except Exception as e:
                        print(f"Error in atexit hook: {e}", file=sys.stderr)

        if not trace_shutdown:
            try:
                atexit._run_exitfuncs()
            except Exception as e:
                print(f"Error in atexit hook: {e}", file=sys.stderr)
    finally:
        # Undo module patches in reverse order (namespace + immutables).
        for undo in reversed(patch_undos):
            undo()

        # Unpatch all types that were modified in-place.
        for cls in list(system.patched_types):
            system.unpatch_type(cls)

        # Uninstall hooks in reverse order.
        for uninstall in reversed(uninstallers):
            uninstall()


# ── Divergence exception ──────────────────────────────────────

class ReplayDivergence(AssertionError):
    """Raised when replay produces different results than recording.

    Attributes
    ----------
    events : list
        The flat list of ``(tag, value)`` events captured during
        recording.  Useful for post-mortem diagnosis.
    consumed : int
        How many events the reader consumed before the divergence
        (or end of replay).
    """

    def __init__(self, message, *, events=None, consumed=0):
        super().__init__(message)
        self.events = events or []
        self.consumed = consumed


# ── Private in-memory writer / reader ─────────────────────────
#
# Self-contained so install_for_pytest has no dependency on the
# proxy test-helpers package.

class _MemoryWriter:
    """Minimal in-memory writer for the test runner."""

    __slots__ = ('events', 'type_serializer', '_pending_stack', '_tracing')

    def __init__(self):
        self.events = []
        self.type_serializer = {}
        self._pending_stack = None
        self._tracing = True

    # no-ops for interfaces the system calls but we don't need
    def bind(self, *a, **kw): pass
    def ext_bind(self, *a, **kw): pass
    def write_call(self, *a, **kw): pass

    def sync(self):
        self._tracing = False

    def stacktrace(self):
        import traceback
        self._pending_stack = traceback.format_stack()

    def write_result(self, *a, **kw):
        stack = self._pending_stack
        self._pending_stack = None
        self.events.append(('result', a[0] if a else kw.get('result'), stack))
        self._tracing = True

    def write_error(self, *a, **kw):
        stack = self._pending_stack
        self._pending_stack = None
        self.events.append(('error', a[0] if a else kw.get('error'), stack))
        self._tracing = True

    def checkpoint(self, value):
        self.events.append(('checkpoint', value, None))

    def function_call(self, qualname, filename, lineno):
        if self._tracing:
            self.events.append(('fcall', qualname, filename, lineno))

    def function_return(self, qualname, filename, lineno):
        if self._tracing:
            self.events.append(('fret', qualname, filename, lineno))


class _MemoryReader:
    """Minimal in-memory reader for the test runner."""

    __slots__ = ('_events', '_pos', '_tracing')

    def __init__(self, events):
        self._events = list(events)
        self._pos = 0
        self._tracing = True

    # no-ops
    def bind(self, *a, **kw): pass

    def sync(self):
        self._tracing = False

    def read_result(self):
        # Skip any fcall/fret events at current position
        while self._pos < len(self._events):
            tag = self._events[self._pos][0]
            if tag in ('fcall', 'fret'):
                self._pos += 1
                continue
            break

        if self._pos >= len(self._events):
            raise ReplayDivergence(
                "replay made more external calls than recording",
                events=self._events, consumed=self._pos)
        event = self._events[self._pos]
        tag, value = event[0], event[1]
        self._pos += 1
        self._tracing = True
        if tag == 'error':
            raise value
        return value

    def checkpoint(self, value):
        # Skip any fcall/fret events at current position
        while self._pos < len(self._events):
            tag = self._events[self._pos][0]
            if tag in ('fcall', 'fret'):
                self._pos += 1
                continue
            break

        if self._pos >= len(self._events):
            raise ReplayDivergence(
                "replay checkpoint beyond recorded events",
                events=self._events, consumed=self._pos)
        event = self._events[self._pos]
        tag, expected = event[0], event[1]
        self._pos += 1
        if value != expected:
            raise ReplayDivergence(
                f"checkpoint divergence at event {self._pos - 1}: "
                f"expected {expected!r}, got {value!r}",
                events=self._events, consumed=self._pos)

    # ── trace comparison (called by sys.monitoring callbacks) ──

    def function_call(self, qualname, filename, lineno):
        if not self._tracing:
            return
        if self._pos >= len(self._events):
            self._report_trace_divergence(
                ('fcall', qualname, filename, lineno), None)
            return
        event = self._events[self._pos]
        if event[0] == 'fcall':
            if event[1] != qualname or event[2] != filename or event[3] != lineno:
                self._report_trace_divergence(
                    ('fcall', qualname, filename, lineno), event)
            self._pos += 1
        else:
            self._report_trace_divergence(
                ('fcall', qualname, filename, lineno), event)

    def function_return(self, qualname, filename, lineno):
        if not self._tracing:
            return
        if self._pos >= len(self._events):
            self._report_trace_divergence(
                ('fret', qualname, filename, lineno), None)
            return
        event = self._events[self._pos]
        if event[0] == 'fret':
            if event[1] != qualname or event[2] != filename or event[3] != lineno:
                self._report_trace_divergence(
                    ('fret', qualname, filename, lineno), event)
            self._pos += 1
        else:
            self._report_trace_divergence(
                ('fret', qualname, filename, lineno), event)

    def _report_trace_divergence(self, replay_event, stored_event):
        import sys as _sys
        w = _sys.stderr.write
        pos = self._pos

        # Count tape events before this position to get tape position
        tape_pos = sum(1 for e in self._events[:pos]
                       if e[0] in ('result', 'error', 'checkpoint'))

        w(f"\n{'='*72}\n")
        w(f"TRACE DIVERGENCE at event {pos} (tape position {tape_pos})\n")
        w(f"{'='*72}\n\n")

        # Show context: surrounding events
        start = max(0, pos - 5)
        end = min(len(self._events), pos + 5)

        w("  Stored (record):\n")
        for i in range(start, end):
            e = self._events[i]
            marker = "  <-- divergence" if i == pos else ""
            if e[0] in ('fcall', 'fret'):
                w(f"    [{i:4d}] {e[0]:5s} {e[1]:40s} {_basename(e[2])}:{e[3]}{marker}\n")
            else:
                try:
                    vr = f"{type(e[1]).__name__}"
                except Exception:
                    vr = "?"
                w(f"    [{i:4d}] {e[0]:5s} {vr}{marker}\n")

        w("\n  Replay produced:\n")
        w(f"    {replay_event[0]:5s} {replay_event[1]:40s} "
          f"{_basename(replay_event[2])}:{replay_event[3]}\n")

        if stored_event:
            w("\n  Stored expected:\n")
            if stored_event[0] in ('fcall', 'fret'):
                w(f"    {stored_event[0]:5s} {stored_event[1]:40s} "
                  f"{_basename(stored_event[2])}:{stored_event[3]}\n")
            else:
                w(f"    {stored_event[0]} (tape event, not trace)\n")
        else:
            w("\n  Stored: end of events\n")

        w(f"{'='*72}\n")


# ── Monitoring tracer (PEP 669, Python 3.12+) ────────────────

def _basename(path):
    """Last component of a file path, for compact display."""
    if '/' in path:
        return path.rsplit('/', 1)[-1]
    return path


class _MonitoringTracer:
    """Feeds ``sys.monitoring`` CALL/PY_RETURN events directly to a target.

    *target* must implement ``function_call(qualname, filename, lineno)``
    and ``function_return(qualname, filename, lineno)`` — i.e. a
    ``_MemoryWriter`` or ``_MemoryReader``.

    Events whose filename contains ``retracesoftware/`` are filtered
    out (gate/proxy noise).

    ``sys.monitoring`` automatically suppresses further events for the
    same tool during a callback, so no re-entrancy guard is needed.
    """

    def __init__(self, target):
        self._target = target
        self._active = False

    def start(self):
        import sys as _sys
        mon = getattr(_sys, 'monitoring', None)
        if mon is None:
            return
        mon.use_tool_id(mon.PROFILER_ID, "tape_tracer")

        target = self._target

        def on_call(code, offset, callable_obj, arg0):
            callee_code = getattr(callable_obj, '__code__', None)
            if callee_code:
                fn = callee_code.co_filename
                if 'retracesoftware' in fn:
                    return
                target.function_call(
                    callee_code.co_qualname, fn,
                    callee_code.co_firstlineno)
            else:
                mod = getattr(callable_obj, '__module__', '') or ''
                if 'retracesoftware' in mod:
                    return
                name = (getattr(callable_obj, '__qualname__', None)
                        or getattr(callable_obj, '__name__', '?'))
                target.function_call(name, f'<{mod}>', 0)

        def on_return(code, offset, retval):
            fn = code.co_filename
            if 'retracesoftware' in fn:
                return
            target.function_return(
                code.co_qualname, fn, code.co_firstlineno)

        mon.register_callback(
            mon.PROFILER_ID, mon.events.CALL, on_call)
        mon.register_callback(
            mon.PROFILER_ID, mon.events.PY_RETURN, on_return)
        mon.set_events(
            mon.PROFILER_ID,
            mon.events.CALL | mon.events.PY_RETURN)
        self._active = True

    def stop(self):
        if not self._active:
            return
        import sys as _sys
        mon = _sys.monitoring
        mon.set_events(mon.PROFILER_ID, 0)
        mon.register_callback(
            mon.PROFILER_ID, mon.events.CALL, None)
        mon.register_callback(
            mon.PROFILER_ID, mon.events.PY_RETURN, None)
        self._active = False


# ── Recording handle ──────────────────────────────────────────

class Recording:
    """Opaque handle returned by ``TestRunner.record``.

    Attributes
    ----------
    result
        The return value of *fn* during recording (``None`` if it raised).
    error : Exception or None
        The exception raised by *fn* during recording, if any.
    events : list
        The raw ``(tag, value)`` event stream.  Mainly useful for
        post-mortem diagnosis — pass the ``Recording`` itself to
        ``replay()``.
    """

    __slots__ = ('events', 'result', 'error')

    def __init__(self, events, result, error):
        self.events = events
        self.result = result
        self.error = error


# ── Test runner ───────────────────────────────────────────────

class TestRunner:
    """Returned by ``install_for_pytest``.

    Three methods cover common pytest patterns:

    ``run(fn, *a, **kw)``
        Record then immediately replay.  Simplest — use when the
        function is self-contained and has no live infrastructure to
        tear down between phases.

    ``record(fn, *a, **kw) → Recording``
        Record only.  Returns a ``Recording`` that carries the event
        stream, result, and any error.

    ``replay(recording, fn, *a, **kw)``
        Replay from a previous ``Recording``.  Raises
        ``ReplayDivergence`` on any mismatch.

    Example — simple (no teardown needed)::

        runner = install_for_pytest(modules=["socket"])

        def test_dns():
            runner.run(socket.getaddrinfo, "example.com", 80)

    Example — server teardown between phases::

        runner = install_for_pytest(modules=["flask"])

        def test_flask(app_server):
            def do_requests():
                ...

            recording = runner.record(do_requests)
            app_server.shutdown()          # free the port / threads
            runner.replay(recording, do_requests)
    """

    __slots__ = ('_system',)

    def __init__(self, system):
        self._system = system

    # ── record ────────────────────────────────────────────────

    def record(self, fn, *args, **kwargs):
        """Run *fn* under recording and return a ``Recording``.

        The recording captures every gate event (results, errors,
        checkpoints).  Pass the returned object to ``replay()`` when
        ready.

        Thread patching is set up so that child threads spawned
        during recording automatically enter the same recording
        context.
        """
        import retracesoftware.functional as functional
        from retracesoftware.install.startthread import patch_thread_start

        writer = _MemoryWriter()
        context = self._system.record_context(writer, stacktraces=True)
        error = None
        result = None

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        uninstall = patch_thread_start(dispatch)

        try:
            with context:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    error = exc
        finally:
            uninstall()

        return Recording(writer.events, result, error)

    # ── replay ────────────────────────────────────────────────

    def replay(self, recording, fn, *args, **kwargs):
        """Run *fn* under replay using a previous ``Recording``.

        Raises ``ReplayDivergence`` if the replay diverges from the
        recording.  If both record and replay raised, the original
        record error is re-raised (so tests can use
        ``pytest.raises``).

        Returns the result from the record phase.
        """
        import retracesoftware.functional as functional
        from retracesoftware.install.startthread import patch_thread_start

        reader = _MemoryReader(recording.events)
        context = self._system.replay_context(reader)

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        uninstall = patch_thread_start(dispatch)

        try:
            try:
                with context:
                    try:
                        replay_result = fn(*args, **kwargs)
                    except ReplayDivergence:
                        raise
                    except Exception as exc:
                        if recording.error is None:
                            raise ReplayDivergence(
                                f"replay raised {type(exc).__name__} "
                                f"but record succeeded",
                                events=recording.events,
                                consumed=reader._pos,
                            ) from exc
                        # Both raised — same code path, not divergence.
                        raise recording.error
            except ReplayDivergence:
                raise
            except Exception as exc:
                raise ReplayDivergence(
                    f"replay diverged: {exc}",
                    events=recording.events,
                    consumed=reader._pos,
                ) from exc
        finally:
            uninstall()

        # Replay succeeded — check record didn't raise
        if recording.error is not None:
            raise ReplayDivergence(
                f"record raised {type(recording.error).__name__} "
                f"but replay succeeded",
                events=recording.events,
                consumed=reader._pos,
            )

        # Verify all recorded events were consumed
        remaining = len(reader._events) - reader._pos
        if remaining > 0:
            raise ReplayDivergence(
                f"{remaining} recorded events were not consumed "
                f"during replay (total {len(reader._events)})",
                events=recording.events,
                consumed=reader._pos,
            )

        # Compare return values
        if replay_result != recording.result:
            raise ReplayDivergence(
                f"return value divergence: "
                f"record returned {recording.result!r}, "
                f"replay returned {replay_result!r}",
                events=recording.events,
                consumed=reader._pos,
            )

        return recording.result

    # ── convenience: record + replay in one call ──────────────

    def run(self, fn, *args, **kwargs):
        """Record *fn*, then immediately replay it.

        Equivalent to::

            recording = runner.record(fn, *args, **kwargs)
            return runner.replay(recording, fn, *args, **kwargs)
        """
        recording = self.record(fn, *args, **kwargs)
        return self.replay(recording, fn, *args, **kwargs)

    # ── diagnose: sequential record then replay with tracing ───

    def diagnose(self, fn, *args, setup=None, **kwargs):
        """Record then replay *fn* with fine-grained call tracing.

        ``sys.monitoring`` (PEP 669) captures Python call/return events
        interleaved with tape events.  During replay the reader
        compares each ``function_call``/``function_return`` against the
        stored recording, printing a divergence report on mismatch.

        Parameters
        ----------
        fn : callable
            The function to record and replay.
        setup : callable, optional
            Called before each phase (e.g. flush modules).
        """
        import sys as _sys

        # ── record phase ──────────────────────────────────────
        writer = _MemoryWriter()
        tracer = _MonitoringTracer(writer)
        record_ctx = self._system.record_context(
            writer, stacktraces=True)

        if setup:
            setup()

        tracer.start()
        try:
            with record_ctx:
                record_result = fn(*args, **kwargs)
        finally:
            tracer.stop()

        # ── replay phase ──────────────────────────────────────
        reader = _MemoryReader(writer.events)
        tracer = _MonitoringTracer(reader)
        replay_ctx = self._system.replay_context(reader)

        if setup:
            setup()

        tracer.start()
        try:
            with replay_ctx:
                replay_result = fn(*args, **kwargs)
        except ReplayDivergence as exc:
            print(f"\nReplay diverged: {exc}", file=_sys.stderr)
            return
        except Exception as exc:
            print(f"\nReplay raised: {exc!r}", file=_sys.stderr)
            return
        finally:
            tracer.stop()

        if record_result != replay_result:
            print(
                f"\nReturn-value mismatch: record={record_result!r}, "
                f"replay={replay_result!r}",
                file=_sys.stderr)


# ── Public API ────────────────────────────────────────────────

def install_for_pytest(modules=None):
    """Install the retrace system for in-process pytest testing.

    Creates a ``System``, installs all hooks (hash patching, trace
    hooks, import hooks with TOML-driven module patcher), and returns
    a ``TestRunner``.

    No teardown is needed — run each library's tests in a separate
    pytest file (separate process).

    Parameters
    ----------
    modules : list[str], optional
        Module names to force-import after install, ensuring they are
        patched before any test runs.  Useful for libraries that must
        be loaded eagerly (e.g. ``["flask", "requests"]``).

    Returns
    -------
    TestRunner
        A runner with ``record()``, ``replay()``, and ``run()``
        methods.

    Example
    -------
    ::

        # test_socket.py
        import socket
        from retracesoftware.install import install_for_pytest

        runner = install_for_pytest(modules=["socket"])

        def test_dns_lookup():
            runner.run(socket.getaddrinfo, "example.com", 80)

        def test_flask_hello(app_server):
            recording = runner.record(make_requests)
            app_server.shutdown()
            runner.replay(recording, make_requests)
    """
    from retracesoftware.proxy.system import System
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.patcher import patch, install_hash_patching
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded
    from retracesoftware.install.hooks import install_trace_hooks, init_weakref

    system = System()

    # ── one-time system setup ─────────────────────────────────
    install_hash_patching(system)
    install_trace_hooks(system.disable_for)
    init_weakref()

    # ── preload commonly-used modules before patching ─────────
    preload = pkgutil.get_data("retracesoftware", "preload.txt")
    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    # ── build the module patcher ──────────────────────────────
    module_config = ModuleConfigResolver()

    def module_patcher(namespace, update_refs):
        name = namespace.get('__name__')
        if name and name in module_config:
            patch(namespace, module_config[name], system, update_refs)

    # ── patch already-loaded modules, install import hooks ────
    patch_already_loaded(module_patcher, module_config)
    install_import_hooks(system.disable_for, module_patcher)

    # ── force-import requested modules so they're patched ─────
    if modules:
        for name in modules:
            importlib.import_module(name)

    return TestRunner(system)
