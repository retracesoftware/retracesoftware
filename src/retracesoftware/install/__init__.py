"""Install helpers for the retrace system.

Provides:

- ``install_retrace`` / ``install_and_run`` for bootstrapping retrace.
- ``install_for_pytest`` for in-process testing with a ``TestRunner``
  that records then replays a function call, raising on divergence.
"""

import importlib
import importlib.resources
import atexit
import os
from contextlib import contextmanager
from retracesoftware import functional
from retracesoftware import utils

_pytest_runner = None
_pytest_installed_modules = set()

def install_retrace(*, system, retrace_file_patterns=None, monitor_level=0, verbose=False, retrace_shutdown=True):
    """Install process-global retrace hooks for a configured ``System``.

    Returns an uninstall callable that removes import/thread/monitoring hooks
    and undoes any module/type mutations tracked through the installation.
    """
    import atexit
    import functools
    import threading
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.installation import Installation
    from retracesoftware.install.patcher import patch
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded

    uninstallers = []
    patch_undos = []

    if monitor_level > 0:
        from retracesoftware.install.monitoring import install_monitoring
        uninstallers.append(install_monitoring(system.checkpoint, monitor_level))

    uninstallers.append(system.install())

    if retrace_shutdown:
        original_atexit_register = atexit.register
        original_atexit_unregister = atexit.unregister
        original_threading_register_atexit = threading._register_atexit
        shutdown_tracing_enabled = True
        wrapped_atexit_callbacks = {}

        def traced_shutdown_callback(function, args, kwargs):
            if shutdown_tracing_enabled:
                return system.run(function, *args, **kwargs)
            return function(*args, **kwargs)

        def register_atexit(function, *args, **kwargs):
            wrapped = functools.partial(traced_shutdown_callback, function, args, kwargs)
            wrapped_atexit_callbacks.setdefault(function, []).append(wrapped)
            original_atexit_register(wrapped)
            return function

        def unregister_atexit(function):
            for wrapped in wrapped_atexit_callbacks.pop(function, ()):
                original_atexit_unregister(wrapped)
            return original_atexit_unregister(function)

        def register_threading_atexit(function, *args, **kwargs):
            return original_threading_register_atexit(
                traced_shutdown_callback,
                function,
                args,
                kwargs,
            )

        atexit.register = register_atexit
        atexit.unregister = unregister_atexit
        threading._register_atexit = register_threading_atexit

        def uninstall_shutdown_tracing():
            nonlocal shutdown_tracing_enabled
            shutdown_tracing_enabled = False
            atexit.register = original_atexit_register
            atexit.unregister = original_atexit_unregister
            threading._register_atexit = original_threading_register_atexit

        uninstallers.append(uninstall_shutdown_tracing)

    try:
        preload = importlib.resources.files("retracesoftware").joinpath("preload.txt").read_bytes()
    except (FileNotFoundError, TypeError):
        preload = b""
    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    module_config = ModuleConfigResolver()
    installation = Installation(system)

    from retracesoftware.install.pathpredicate import load_patterns, make_pathpredicate
    pathpredicate = make_pathpredicate(load_patterns(retrace_file_patterns), verbose=verbose)

    def module_patcher(namespace, update_refs, module_name=None):
        name = module_name or namespace.get('__name__')
        if name and name in module_config:
            undo = patch(
                namespace,
                module_config[name],
                installation,
                update_refs=update_refs,
                pathpredicate=pathpredicate,
            )
            if undo is not None:
                patch_undos.append(undo)

    patch_already_loaded(module_patcher, module_config)
    uninstallers.append(install_import_hooks(system.disable_for, module_patcher))

    uninstalled = False

    def uninstall():
        nonlocal uninstalled
        if uninstalled:
            return
        uninstalled = True
        for undo in reversed(patch_undos):
            undo()
        installation.uninstall()
        for uninstall_one in reversed(uninstallers):
            uninstall_one()

    return uninstall

def install_and_run(*, system, options, function, args = (), kwargs = {}, post_install = None):
    uninstall = install_retrace(
        system = system, 
        monitor_level=getattr(options, 'monitor', 0),
        retrace_file_patterns=getattr(options, 'retrace_file_patterns', None),
        verbose=options.verbose,
        retrace_shutdown=options.trace_shutdown)

    if post_install is not None:
        uninstall = utils.runall(post_install, uninstall)

    if options.trace_shutdown:
        atexit.register(uninstall)
        return system.run(function, *args, **kwargs)
    else:
        try:
            return system.run(function, *args, **kwargs)
        finally:
            uninstall()

def patch_fork_for_replay(disable_for):
    _gate_fork = os.fork

    def post_fork_replay(recorded_result):
        if recorded_result == 0:
            pid = disable_for(_gate_fork)()
            if pid != 0:
                disable_for(os._exit)(0)
        return recorded_result

    os.fork = functional.sequence(_gate_fork, post_fork_replay)

    def uninstall_fork():
        os.fork = _gate_fork
        
    return uninstall_fork


def install_checkpoint_hooks(checkpoint_fn, monitor_level):
    if monitor_level <= 0:
        return utils.noop

    from retracesoftware.install.monitoring import install_monitoring

    return install_monitoring(checkpoint_fn, monitor_level)


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


@contextmanager
def _temporary_runner_context(
    system,
    *,
    bind,
    primary_hooks,
    secondary_hooks,
    lifecycle_hooks,
    checkpoint,
    ext_execute=None,
):
    from retracesoftware.proxy.system import CallHooks, LifecycleHooks

    old_bind = system.bind
    old_async_new_patched = system.async_new_patched
    old_primary_hooks = system.primary_hooks
    old_secondary_hooks = system.secondary_hooks
    old_lifecycle_hooks = system.lifecycle_hooks
    old_checkpoint = system.checkpoint
    old_ext_execute = system.ext_execute

    def on_async_new_patched(obj):
        if system.primary_hooks and system.primary_hooks.on_call:
            system.primary_hooks.on_call(utils.create_stub_object, type(obj))

        bind(obj)

        if system.secondary_hooks and system.secondary_hooks.on_result:
            system.secondary_hooks.on_result(obj)

    system.bind = utils.runall(system.is_bound.add, bind)
    system.async_new_patched = utils.runall(system.is_bound.add, on_async_new_patched)
    system.primary_hooks = CallHooks(*primary_hooks)
    system.secondary_hooks = CallHooks(*secondary_hooks)
    system.lifecycle_hooks = LifecycleHooks(*lifecycle_hooks)
    system.checkpoint = checkpoint

    if ext_execute is not None:
        system.ext_execute = ext_execute

    try:
        with system.context():
            yield
    finally:
        system.bind = old_bind
        system.async_new_patched = old_async_new_patched
        system.primary_hooks = old_primary_hooks
        system.secondary_hooks = old_secondary_hooks
        system.lifecycle_hooks = old_lifecycle_hooks
        system.checkpoint = old_checkpoint
        system.ext_execute = old_ext_execute


def _record_context_for_runner(system, writer, *, on_start=None, on_end=None):
    checkpoint_call = functional.pack_call(
        1,
        lambda fn, args, kwargs: writer.checkpoint(
            {
                "function": fn,
                "args": args,
                "kwargs": kwargs,
            }
        ),
    )

    return _temporary_runner_context(
        system,
        bind=writer.bind,
        primary_hooks=(writer.write_call, writer.write_result, writer.write_error),
        secondary_hooks=(
            checkpoint_call,
            writer.checkpoint,
            functional.sequence(functional.positional_param(1), writer.checkpoint),
        ),
        lifecycle_hooks=(utils.runall(on_start), utils.runall(on_end)),
        checkpoint=writer.checkpoint,
    )


def _replay_context_for_runner(system, reader, *, on_start=None, on_end=None):
    checkpoint_call = functional.pack_call(
        1,
        lambda fn, args, kwargs: reader.checkpoint(
            {
                "function": fn,
                "args": args,
                "kwargs": kwargs,
            }
        ),
    )

    return _temporary_runner_context(
        system,
        bind=reader.bind,
        primary_hooks=(reader.write_call, None, None),
        secondary_hooks=(
            checkpoint_call,
            reader.checkpoint,
            functional.sequence(functional.positional_param(1), reader.checkpoint),
        ),
        lifecycle_hooks=(utils.runall(on_start), utils.runall(on_end)),
        checkpoint=reader.checkpoint,
        ext_execute=functional.repeatedly(reader.read_result),
    )


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

    __slots__ = ('_system', '_install_session')

    def __init__(self, system, install_session=None):
        self._system = system
        self._install_session = install_session

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
        from retracesoftware.testing.protocol_memory import MemoryWriter
        from retracesoftware.install.startthread import patch_thread_start

        callback_binding_hooks = {}
        if self._install_session is not None:
            callback_binding_hooks = self._install_session.callback_binding_hooks(
                self._system.bind
            )

        writer = MemoryWriter(thread=threading.get_ident)
        context = _record_context_for_runner(self._system,
            writer,
            **callback_binding_hooks,
        )
        error = None
        result = None

        uninstall_monitor = install_checkpoint_hooks(
            self._system.disable_for(writer.monitor_event),
            monitor,
        )

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        uninstall_thread_patch = patch_thread_start(dispatch)

        try:
            with context:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    error = exc
        finally:
            uninstall_thread_patch()
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
        from retracesoftware.testing.protocol_memory import MemoryReader
        from retracesoftware.install.startthread import patch_thread_start

        callback_binding_hooks = {}
        if self._install_session is not None:
            callback_binding_hooks = self._install_session.callback_binding_hooks(
                self._system.bind
            )

        reader = MemoryReader(recording.tape, timeout=timeout,
                              monitor_enabled=(monitor > 0))
        context = _replay_context_for_runner(self._system,
            reader,
            **callback_binding_hooks,
        )

        def _verify(value):
            reader.monitor_checkpoint(value)

        uninstall_monitor = install_checkpoint_hooks(
            self._system.disable_for(_verify),
            monitor,
        )

        def wrapper(fn):
            def wrapped(*a, **kw):
                with context:
                    return fn(*a, **kw)
            return wrapped

        dispatch = self._system.create_dispatch(
            external=wrapper,
            internal=functional.identity,
            disabled=functional.identity)

        uninstall_thread_patch = patch_thread_start(dispatch)

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
            uninstall_thread_patch()
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

    def diagnose(self, fn, *args, setup=None, timeout=10, monitor=0, **kwargs):
        """Record, optionally reset state, then replay *fn*.

        This is a convenience for tests that need a hook between phases,
        such as clearing imported modules or tearing down live resources.
        """
        recording = self.record(fn, *args, monitor=monitor, **kwargs)
        if setup is not None:
            setup()
        return self.replay(
            recording,
            fn,
            *args,
            timeout=timeout,
            monitor=monitor,
            **kwargs,
        )


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
    global _pytest_runner, _pytest_installed_modules

    modules = tuple(modules or ())

    if _pytest_runner is not None:
        for name in modules:
            if name not in _pytest_installed_modules:
                importlib.import_module(name)
                _pytest_installed_modules.add(name)
        return _pytest_runner

    from retracesoftware.proxy.system import System
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.installation import Installation
    from retracesoftware.install.patcher import patch, install_hash_patching
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded
    from retracesoftware.install.hooks import init_weakref
    from retracesoftware.install.session import InstallSession

    system = System()
    install_session = InstallSession()

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

    def module_patcher(namespace, update_refs, module_name=None):
        name = module_name or namespace.get('__name__')
        if name and name in module_config:
            installation = Installation(
                system,
                install_session=install_session,
                update_refs=update_refs,
            )
            patch(
                namespace,
                module_config[name],
                installation,
            )

    # ── patch already-loaded modules, install import hooks ────
    patch_already_loaded(module_patcher, module_config)
    install_import_hooks(system.disable_for, module_patcher)

    # ── force-import requested modules so they're patched ─────
    if modules:
        for name in modules:
            importlib.import_module(name)
            _pytest_installed_modules.add(name)

    _pytest_runner = TestRunner(system, install_session)
    return _pytest_runner


from retracesoftware.protocol.record import stream_writer
