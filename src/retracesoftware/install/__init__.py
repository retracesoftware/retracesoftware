"""Install helpers for the retrace system.

Provides:

- ``run_with_context`` for bootstrapping the retrace hook machinery.
- ``install_for_pytest`` for in-process testing with a ``TestRunner``
  that records then replays a function call, raising on divergence.
"""

import importlib
import importlib.resources

def run_with_context(system,
                     thread_id,
                     context, argv, wrap_callback, trace_shutdown=False, on_ready=None,
                     monitor_level=0, monitor_fn=None, retrace_file_patterns=None,
                     verbose=False):
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
    monitor_level : int
        ``sys.monitoring`` granularity (0=off, 1–3=increasingly fine).
    monitor_fn : callable or None
        Checkpoint function for monitoring events, already wrapped
        with ``system.disable_for``.  None when ``monitor_level`` is 0.
    retrace_file_patterns : str or None
        Path to an extra file of regex patterns for path-based
        retrace filtering.  ``None`` uses shipped defaults only.
    verbose : bool
        If True, enable verbose logging (e.g. path predicate decisions).
    """
    import sys
    import atexit
    import retracesoftware.functional as functional
    import retracesoftware.utils as utils
    from retracesoftware.run import run_python_command, wait_for_non_daemon_threads
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.patcher import patch, install_hash_patching
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded
    from retracesoftware.install.hooks import install_weakref_hooks, init_weakref

    counter = utils.ThreadLocal(0)

    thread_id.set(())

    def inc(x): return x + 1

    def next_thread_id():
        if thread_id.get() and system._in_sandbox:
            return thread_id.get() + (counter.update(inc),)
        return None

    utils.add_thread_middleware(lambda: thread_id.context(next_thread_id()))
    utils.add_thread_middleware(functional.constantly(context))

    uninstallers = []

    # ── one-time system setup ─────────────────────────────────
    uninstallers.append(install_hash_patching(system))
    uninstallers.append(install_weakref_hooks(system, wrap_callback))
    init_weakref()

    if monitor_level > 0:
        from retracesoftware.install.monitoring import install_monitoring
        uninstallers.append(install_monitoring(system, monitor_fn, monitor_level))

    # ── preload commonly-used modules before patching ─────────
    try:
        preload = importlib.resources.files("retracesoftware").joinpath("preload.txt").read_bytes()
    except (FileNotFoundError, TypeError):
        preload = b""
    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    # ── build the module patcher ──────────────────────────────
    module_config = ModuleConfigResolver()
    patch_undos = []

    from retracesoftware.install.pathpredicate import load_patterns, make_pathpredicate
    pathpredicate = make_pathpredicate(load_patterns(retrace_file_patterns), verbose=verbose)

    def module_patcher(namespace, update_refs):
        name = namespace.get('__name__')
        if name and name in module_config:
            undo = patch(namespace, module_config[name], system, update_refs,
                         pathpredicate=pathpredicate)
            patch_undos.append(undo)

    # ── patch already-loaded modules, install hooks, run ──────
    patch_already_loaded(module_patcher, module_config)
    uninstallers.append(install_import_hooks(system.disable_for, module_patcher))

    if on_ready:
        on_ready()

    try:
        with context:
            for cls in sorted(system.patched_types, key=lambda c: c.__qualname__):
                system._bind(cls)
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
        if hasattr(system, 'unpatch_type'):
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
    tape : list
        The flat tagged message tape captured during recording.
        Useful for post-mortem diagnosis.
    """

    def __init__(self, message, *, tape=None):
        super().__init__(message)
        self.tape = tape or []


# ── In-memory writer / reader ─────────────────────────────────
#
# Backed by the MessageStream abstraction in proxy.messagestream.
# The writer produces a flat tagged tape; the reader wraps a
# MessageStream around it.



# ── Recording handle ──────────────────────────────────────────

class Recording:
    """Opaque handle returned by ``TestRunner.record``.

    Attributes
    ----------
    result
        The return value of *fn* during recording (``None`` if it raised).
    error : Exception or None
        The exception raised by *fn* during recording, if any.
    tape : list
        The raw flat tagged message tape.  Mainly useful for
        post-mortem diagnosis — pass the ``Recording`` itself to
        ``replay()``.
    """

    __slots__ = ('tape', 'result', 'error')

    def __init__(self, tape, result, error):
        self.tape = tape
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

    def record(self, fn, *args, monitor=0, **kwargs):
        """Run *fn* under recording and return a ``Recording``.

        The recording captures every gate event (results, errors,
        checkpoints) as a flat tagged tape.  Pass the returned object
        to ``replay()`` when ready.

        A ``thread`` callable (``threading.get_ident``) is passed to
        the writer so thread-switch markers are emitted when multiple
        threads write to the tape.

        Thread patching is set up so that child threads spawned
        during recording automatically enter the same recording
        context.

        Parameters
        ----------
        monitor : int
            ``sys.monitoring`` granularity (0=off, 1–3).
        """
        import threading
        import retracesoftware.functional as functional
        from retracesoftware.proxy.messagestream import MemoryWriter
        from retracesoftware.install.startthread import patch_thread_start

        writer = MemoryWriter(thread=threading.get_ident)
        context = self._system.record_context(writer)
        error = None
        result = None

        uninstall_monitor = None
        if monitor > 0:
            from retracesoftware.install.monitoring import install_monitoring
            monitor_fn = self._system.disable_for(writer.monitor_event)
            uninstall_monitor = install_monitoring(
                self._system, monitor_fn, monitor)

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        patch_thread_start(dispatch)

        try:
            with context:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    error = exc
        finally:
            if uninstall_monitor:
                uninstall_monitor()

        return Recording(writer.tape, result, error)

    # ── replay ────────────────────────────────────────────────

    def replay(self, recording, fn, *args, timeout=10, monitor=0, **kwargs):
        """Run *fn* under replay using a previous ``Recording``.

        Raises ``ReplayDivergence`` if the replay diverges from the
        recording.  If both record and replay raised, the original
        record error is re-raised (so tests can use
        ``pytest.raises``).

        *timeout* (seconds) is passed to ``MemoryReader`` so the
        demux raises ``TimeoutError`` instead of blocking forever
        when threads diverge.  Default is 10s.

        Parameters
        ----------
        monitor : int
            ``sys.monitoring`` granularity (0=off, 1–3).  Must match
            the level used during recording.

        Returns the result from the record phase.
        """
        import retracesoftware.functional as functional
        from retracesoftware.proxy.messagestream import MemoryReader
        from retracesoftware.install.startthread import patch_thread_start

        reader = MemoryReader(recording.tape, timeout=timeout,
                              monitor_enabled=(monitor > 0))
        context = self._system.replay_context(reader)

        uninstall_monitor = None
        if monitor > 0:
            from retracesoftware.install.monitoring import install_monitoring
            def _verify(value):
                reader.monitor_checkpoint(value)
            monitor_fn = self._system.disable_for(_verify)
            uninstall_monitor = install_monitoring(
                self._system, monitor_fn, monitor)

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        patch_thread_start(dispatch)

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
                                tape=recording.tape,
                            ) from exc
                        # Both raised — same code path, not divergence.
                        raise recording.error
            except ReplayDivergence:
                raise
            except Exception as exc:
                raise ReplayDivergence(
                    f"replay diverged: {exc}",
                    tape=recording.tape,
                ) from exc

            # Replay succeeded — check record didn't raise
            if recording.error is not None:
                raise ReplayDivergence(
                    f"record raised {type(recording.error).__name__} "
                    f"but replay succeeded",
                    tape=recording.tape,
                )

            # Compare return values
            if replay_result != recording.result:
                raise ReplayDivergence(
                    f"return value divergence: "
                    f"record returned {recording.result!r}, "
                    f"replay returned {replay_result!r}",
                    tape=recording.tape,
                )

            # Check for unconsumed tape entries
            leftover = reader.remaining
            if leftover > 0:
                raise ReplayDivergence(
                    f"tape has {leftover} unconsumed entries "
                    f"(replay consumed fewer events than record produced)",
                    tape=recording.tape,
                )

            return recording.result
        finally:
            if uninstall_monitor:
                uninstall_monitor()

    # ── convenience: record + replay in one call ──────────────

    def run(self, fn, *args, timeout=10, monitor=0, **kwargs):
        """Record *fn*, then immediately replay it.

        Equivalent to::

            recording = runner.record(fn, *args, monitor=monitor, **kwargs)
            return runner.replay(recording, fn, *args, monitor=monitor, **kwargs)
        """
        recording = self.record(fn, *args, monitor=monitor, **kwargs)
        return self.replay(recording, fn, *args, timeout=timeout,
                           monitor=monitor, **kwargs)


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
    from retracesoftware.install.hooks import init_weakref

    system = System()

    # ── one-time system setup ─────────────────────────────────
    install_hash_patching(system)
    init_weakref()

    # ── preload commonly-used modules before patching ─────────
    try:
        preload = importlib.resources.files("retracesoftware").joinpath("preload.txt").read_bytes()
    except (FileNotFoundError, TypeError):
        preload = b""
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


# ── stream.writer → protocol.Writer adapter ───────────────────────

def stream_writer(writer, stackfactory = None, on_write_error = None):
    """Adapt a ``stream.writer`` to the ``protocol.Writer`` interface.

    Returns a ``SimpleNamespace`` whose attributes map directly to
    stream handles — no lambdas, no method dispatch overhead.

    Parameters
    ----------
    writer : retracesoftware.stream.writer
        The underlying stream writer.
    stackfactory : utils.StackFactory or None
        When provided, ``stacktrace()`` captures a stack delta and
        writes it to a ``STACKTRACE`` handle on the stream.

    Example
    -------
        from retracesoftware.stream import writer as stream_writer_cls
        from retracesoftware.install import stream_writer

        sw = stream_writer_cls(path, thread=thread)
        pw = stream_writer(sw, stackfactory=sf)

        with system.record_context(pw):
            ...
    """
    from types import SimpleNamespace

    if stackfactory:
        stacktrace_handle = writer.handle('STACKTRACE')
        def stacktrace():
            stacktrace_handle(stackfactory.delta())
    else:
        stacktrace = None

    _write_error = writer.handle('ERROR')
    def write_error(exc_type, exc_value, exc_tb):
        _write_error(exc_type, exc_value)

    def bind_write_error(func):
        from retracesoftware import utils

        if on_write_error:
            return utils.observer(function = func, on_error = on_write_error)
        else:
            return func
        
    return SimpleNamespace(
        type_serializer = writer.type_serializer,
        bind         = bind_write_error(writer.bind),
        ext_bind     = bind_write_error(writer.ext_bind),
        sync         = bind_write_error(writer.handle('SYNC')),
        write_call   = bind_write_error(writer.handle('CALL')),
        write_result = bind_write_error(writer.handle('RESULT')),
        write_error  = bind_write_error(write_error),
        checkpoint   = bind_write_error(writer.handle('CHECKPOINT')),
        stacktrace   = bind_write_error(stacktrace))
