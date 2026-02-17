import _thread
import threading

def patch_thread_start(wrap_thread_function):
    """Patch ``_thread.start_new_thread`` to apply *wrap_thread_function*.

    *wrap_thread_function* is a ``fn -> fn`` callable that wraps the
    thread target.  Typically built via ``system.create_dispatch``
    so that the wrapper is only applied when a record/replay context
    is active (external gate set).

    Multiple calls stack composably — each captures the current
    ``start_new_thread`` as its ``original``.  Uninstalling in
    reverse order cleanly pops the stack.

    Parameters
    ----------
    wrap_thread_function : callable
        Called with the thread function; must return a (possibly
        wrapped) function with the same ``(*args, **kwargs)``
        signature.

    Returns
    -------
    callable
        An uninstall function that restores ``_thread.start_new_thread``
        and ``threading._start_new_thread`` to their values before
        this patch was applied.
    """
    original = _thread.start_new_thread

    def patched_start(fn, args, kwargs={}):
        return original(wrap_thread_function(fn), args, kwargs)

    _thread.start_new_thread = patched_start
    threading._start_new_thread = patched_start

    def uninstall():
        _thread.start_new_thread = original
        threading._start_new_thread = original

    return uninstall
