import sys
import os
import runpy
import builtins
import atexit
import retracesoftware.utils as utils


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
    """Run a Python app from a command list.

    Supports:
        ['-m', 'module', 'arg1', ...]     → like `python -m module arg1 ...`
        ['script.py', 'arg1', ...]        → like `python script.py arg1 ...`
    """
    if not argv:
        print("Error: No command provided", file=sys.stderr)
        return 1

    original_argv = sys.argv[:]

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

            if not script_path.endswith(".py"):
                print(f"Error: Not a Python script: {script_path}", file=sys.stderr)
                return 1

            sys.argv = [script_path] + script_args
            runpy.run_path(script_path, run_name="__main__")
            return 0

    except ModuleNotFoundError:
        print(f"Error: No module named '{argv[1]}'", file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv

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
