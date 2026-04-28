"""Install helpers for the retrace system."""

import importlib
import importlib.resources
import atexit
import os
from pathlib import Path
import tomllib
from retracesoftware import functional
from retracesoftware import utils
from retracesoftware.protocol.record import stream_writer

__all__ = [
    "install_retrace",
    "install_and_run",
    "patch_fork_for_replay",
    "ReplayDivergence",
    "Recording",
    "stream_writer",
]


def _user_module_config_exists(module_name):
    user_dir = Path(os.environ.get("RETRACE_MODULES_PATH", ".retrace/modules"))
    if not user_dir.is_dir():
        return False

    for filepath in sorted(user_dir.glob("*.toml")):
        raw = tomllib.loads(filepath.read_text(encoding="utf-8"))
        if all(isinstance(value, dict) for value in raw.values()):
            if module_name in raw:
                return True
        elif filepath.stem == module_name:
            return True

    return False

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
    default_io_config = not _user_module_config_exists("_io")
    installation = Installation(system)

    from retracesoftware.install.pathpredicate import load_patterns, make_pathpredicate
    pathpredicate = make_pathpredicate(load_patterns(retrace_file_patterns), verbose=verbose)
    io_pathpredicate = make_pathpredicate([], verbose=verbose)

    def module_patcher(namespace, update_refs, module_name=None):
        name = module_name or namespace.get('__name__')
        if name and name in module_config:
            active_pathpredicate = (
                io_pathpredicate if default_io_config and name == "_io"
                else pathpredicate
            )
            undo = patch(
                namespace,
                module_config[name],
                installation,
                update_refs=update_refs,
                pathpredicate=active_pathpredicate,
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
# Backed by the in-memory MemoryTape protocol helpers.
# The writer produces a flat tagged tape; the reader wraps a
# MessageStream around it.



# ── Recording handle ──────────────────────────────────────────

class Recording:
    """Opaque handle returned by a record phase.

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
