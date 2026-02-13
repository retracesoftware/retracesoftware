import sys
import os
import runpy
from pathlib import Path
# from retracesoftware.install.phases import *
import pkgutil
import retracesoftware.functional as functional
import builtins
import importlib
import _imp
import importlib._bootstrap_external as _bootstrap_external
import atexit
import threading
import _signal
import retracesoftware.utils as utils
from retracesoftware.install.patcher import patch_module, create_patcher, patch_imported_module
from retracesoftware.proxy.startthread import patch_thread_start
from retracesoftware.install.replace import update

thread_states = [
    "disabled", # Default state when retrace is disabled for a thread
    "internal", # Default state when retrace is disabled for a thread
    "external", # When target thread is running outside the python system to be recorded
    "retrace", # When target thread is running outside the retrace system
    "importing", # When target thread is running outside the retrace system
    "gc", # When the target thread is running inside the pyton garbage collector
]

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

from retracesoftware.modules import ModuleConfigResolver

def wait_for_non_daemon_threads(timeout=None):
    """
    Wait for all non-daemon threads to finish, just like Python does on exit.
    
    Args:
        timeout (float, optional): Max seconds to wait. None = wait forever.
    
    Returns:
        bool: True if all threads finished, False if timeout exceeded.
    """
    import threading
    import time

    start_time = time.time()
    main_thread = threading.main_thread()

    while True:
        # Get all active threads
        active = threading.enumerate()

        # Filter: non-daemon and not the main thread
        non_daemon_threads = [
            t for t in active
            if t is not main_thread and not t.daemon
        ]

        if not non_daemon_threads:
            return True  # All done!

        # Check timeout
        if timeout is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                print(f"Timeout: {len(non_daemon_threads)} thread(s) still alive")
                return False

        # Sleep briefly to avoid busy-wait
        time.sleep(0.1)

def run_python_command(argv):
    """
    Run a Python app from a command list using runpy.

    Supports:
        ['-m', 'module', 'arg1', ...]     → like `python -m module arg1 ...`
        ['script.py', 'arg1', ...]        → like `python script.py arg1 ...`

    Args:
        argv: List of command-line arguments (first item is either '-m' or script path)

    Returns:
        Exit code (0 on success, 1+ on error)
    """
    if not argv:
        print("Error: No command provided", file=sys.stderr)
        return 1

    original_argv = sys.argv[:]
    original_cwd = os.getcwd()

    try:
        if argv[0] == '-m':
            if len(argv) < 2:
                print("Error: -m requires a module name", file=sys.stderr)
                return 1
            module_name = argv[1]
            module_args = argv[2:]
            sys.argv = ['-m', module_name] + module_args
            runpy.run_module(module_name, run_name="__main__")
            return 0

        else:
            script_path = argv[0]
            script_args = argv[1:]
            path = Path(script_path)

            if not path.exists():
                print(f"Error: Script not found: {script_path}", file=sys.stderr)
                return 1

            if path.suffix != ".py":
                print(f"Error: Not a Python script: {script_path}", file=sys.stderr)
                return 1

            # Use the path as given (relative or absolute) to preserve cwd-relative paths
            # for stack trace normalization during record/replay
            sys.argv = [script_path] + script_args

            runpy.run_path(script_path, run_name="__main__")
            return 0

    except ModuleNotFoundError:
        print(f"Error: No module named '{argv[1]}'", file=sys.stderr)
        return 1
    # except Exception as e:
    #     print(f"Error1234: {e}", file=sys.stderr)
    #     raise e
    finally:
        sys.argv = original_argv
        os.chdir(original_cwd)

def patch_import(thread_state, patcher, sync, checkpoint):
    # builtins.__import__ = thread_state.dispatch(builtins.__import__, internal = sync(thread_state.wrap('importing', builtins.__import__)))
    # bi = builtins.__import__
    # def foo(*args, **kwargs):
    #     print(f'in patched __import__: {thread_state.value} {args[0]}')
    #     if thread_state.value == 'internal':
    #         with thread_state.select('importing'):
    #             return bi(*args, **kwargs)
    #     else:
    #         return bi(*args, **kwargs)
        
    # builtins.__import__ = foo

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
    # _imp.exec_dynamic = lambda mod: utils.sigtrap(f'{thread_state.value} - {mod}')
    # _imp.exec_builtin = lambda mod: utils.sigtrap(f'{thread_state.value} - {mod}')

    _imp.exec_builtin = thread_state.dispatch(_imp.exec_builtin, 
        importing = functional.juxt(
                        thread_state.wrap('internal', _imp.exec_builtin), 
                        patch))

    # def runpy_exec(source, globals = None, locals = None):
    #     print(f'In runpy exec!!!!!')
    #     return builtins.exec(source, globals, locals)
    
    # utils.update(runpy, "_run_code",
    #              utils.wrap_func_with_overrides,
    #              exec = runpy_exec)

    utils.update(_bootstrap_external._LoaderBasics, "exec_module", 
                 utils.wrap_func_with_overrides,
                 exec = thread_state.dispatch(builtins.exec, importing = thread_state.wrap('internal', sync(exec))))

# preload = [
#     "logging",
#     "pathlib",
#     "_signal",
#     "_posixsubprocess",
#     "socket",
#     "select",
#     "ssl",
#     "random",
#     "email",
#     "email.errors",
#     "http.client",

#     "json",
#     "typing",
#     "queue",
#     "mimetypes",
#     "tempfile",
#     "zipfile",
#     "importlib.resources",
#     "importlib.metadata",
#     "encodings.idna"

#     # "http.client",
#     # "queue",
#     # "mimetypes",
#     # "encodings.idna",
#     # "hmac",
#     # "ipaddress",
#     # "tempfile",
#     # "zipfile",
#     # "importlib.resources",
#     # "importlib.metadata",
#     # "atexit",
#     # "weakref"
#   ]

# def debugger_is_active():
#     return sys.gettrace() and 'debugpy' in sys.modules

def init_weakref():
    import weakref
    def dummy_callback():
        pass

    class DummyTarget:
        pass

    f = weakref.finalize(DummyTarget(), dummy_callback)
    f.detach()

def wrapped_weakref(ref, thread_state, wrap_callback):
    orig_new = ref.__new__

    def __new__(cls, ob, callback=None, **kwargs):
        return orig_new(cls, ob, wrap_callback(callback) if callback else None)
    
    return type('ref', (ref, ), {'__new__': thread_state.dispatch(orig_new, internal = __new__)})

def patch_weakref(thread_state, wrap_callback):
    import _weakref

    update(_weakref.ref, wrapped_weakref(_weakref.ref, thread_state, wrap_callback))
    # _weakref.ref = wrapped_weakref(_weakref.ref)

# def patch_signal(thread_state, wrap_callback):  

#     def wrap_handler(handler):
#         return utils.observer(on_call = ..., function = handler)

#     _signal.signal = 
#     update(_signal.signal, thread_state.dispatch(_signal.signal, internal = thread_state.wrap('internal', _signal.signal)))



def install(system):

    patch_weakref(thread_state = system.thread_state, wrap_callback = system.wrap_weakref_callback)

    init_weakref()
    
    preload = pkgutil.get_data("retracesoftware", "preload.txt")

    for name in preload.decode("utf-8").splitlines():
        try:
            importlib.import_module(name.strip())
        except ModuleNotFoundError:
            pass

    # if 'pydevd' in sys.modules:
    #     utils.update(sys.modules['pydevd'].PyDB, 'enable_tracing', system.disable_for)
    #     utils.update(sys.modules['pydevd'].PyDB, 'set_suspend', system.disable_for)
    #     utils.update(sys.modules['pydevd'].PyDB, 'do_wait_suspend', system.disable_for)

    # if '_pydevd_bundle.pydevd_trace_dispatch_regular' in sys.modules:
    #     mod = sys.modules['_pydevd_bundle.pydevd_trace_dispatch_regular']
    #     utils.update(mod.ThreadTracer, '__call__', system.disable_for)
        
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
    patch_import(thread_state = system.thread_state, 
                 patcher = patch_imported, 
                 sync = system.sync, 
                 checkpoint = system.checkpoint)

    # print(f'MODULES: {list(sys.modules.keys())}')

    importlib.import_module = \
        system.thread_state.dispatch(system.disable_for(importlib.import_module),
                              internal = system.thread_state.wrap('importing', importlib.import_module))


    system.checkpoint('About to patch preload libraries')

    system.checkpoint('system patched...')

def run_with_retrace(system, argv, trace_shutdown = False):

    def runpy_exec(source, globals = None, locals = None):
        with system.thread_state.select('internal'):
            return builtins.exec(source, globals, locals)
    
    utils.update(runpy, "_run_code",
                 utils.wrap_func_with_overrides,
                 exec = runpy_exec)

    try:
        run_python_command(argv)
    finally:
        wait_for_non_daemon_threads()
        try:
            if trace_shutdown:
                with system.thread_state.select('internal'):
                    atexit._run_exitfuncs()
            else:
                atexit._run_exitfuncs()
        except Exception as e:
            print(f"Error in atexit hook: {e}", file=sys.stderr)


def run_with_context(system, context, argv, trace_shutdown=False):
    """Run a Python command inside a System context (record or replay).

    Parameters
    ----------
    system : System
        The proxy system (new gate-based System from proxy/system.py).
    context : context manager
        A ``system.record_context(writer)`` or
        ``system.replay_context(reader)`` — any context manager that
        activates the system's gates for the duration.
    argv : list[str]
        Command-line arguments: ``['-m', 'module', ...]`` or
        ``['script.py', ...]``.
    trace_shutdown : bool
        If True, atexit hooks run inside the context so their I/O
        is recorded/replayed.  If False, atexit hooks run after
        the context exits.
    """
    from retracesoftware.modules import ModuleConfigResolver
    from retracesoftware.install.patcher import patch, install_hash_patching
    from retracesoftware.install.importhook import install_import_hooks, patch_already_loaded

    # ── one-time system setup ─────────────────────────────────
    install_hash_patching(system)

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

    # ── patch already-loaded modules, install hooks, run ──────
    patch_already_loaded(module_patcher, module_config)
    install_import_hooks(system.disable_for, module_patcher)

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
