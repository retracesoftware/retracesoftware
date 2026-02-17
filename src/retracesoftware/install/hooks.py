"""Runtime hooks for the gate-based System.

Provides install functions for trace/profile hooks and weakref
callback wrapping.  These are thin wrappers around the gate
primitives — they don't know about recording or replaying, just
about disabling/enabling gates and conditionally wrapping callbacks.

Each install function returns an **uninstall** callable that restores
the original state, allowing the system to be cleanly torn down
(e.g. for running record/replay inside pytest).

Usage::

    from retracesoftware.install.hooks import (
        install_trace_hooks,
        install_weakref_hooks,
        init_weakref,
    )

    uninstall_trace = install_trace_hooks(system.disable_for)
    uninstall_weakref = install_weakref_hooks(system, wrap_callback)
    init_weakref()

    # ... run ...

    uninstall_weakref()
    uninstall_trace()
"""

import sys
import threading

import retracesoftware.functional as functional
from retracesoftware.install.replace import update


# ── trace / profile hooks ────────────────────────────────────

def install_trace_hooks(disable_for):
    """Wrap ``sys.settrace``, ``sys.setprofile``, and ``threading.settrace``.

    Trace and profile hooks fire on every Python function call.  If
    the proxy gates are active, they would trigger on the hook itself
    and cause infinite recursion.  This function wraps the ``set*``
    APIs so that any installed hook runs with both gates disabled.

    The wrapping is recursive: if a trace function returns another
    callable (as ``sys.settrace`` callbacks do), the returned callable
    is also wrapped.

    Parameters
    ----------
    disable_for : callable(fn) → fn
        Wraps a function so both proxy gates are temporarily cleared
        for its duration.  Typically ``system.disable_for``.

    Returns
    -------
    callable
        An uninstall function that restores the original ``settrace``,
        ``setprofile``, and ``threading.settrace``.
    """
    orig_settrace = sys.settrace
    orig_setprofile = sys.setprofile
    orig_threading_settrace = threading.settrace
    orig_trace = sys.gettrace()
    orig_profile = sys.getprofile()

    def recursive_disable(func):
        """Wrap *func* (and its return value, recursively) with disable_for."""
        if not callable(func):
            return func
        disabled = disable_for(func)
        def wrapped(*args, **kwargs):
            return recursive_disable(disabled(*args, **kwargs))
        return wrapped

    sys.settrace = functional.sequence(recursive_disable, sys.settrace)
    sys.setprofile = functional.sequence(recursive_disable, sys.setprofile)
    threading.settrace = functional.sequence(recursive_disable, threading.settrace)

    # Re-apply current hooks so they're wrapped immediately.
    sys.settrace(sys.gettrace())
    sys.setprofile(sys.getprofile())

    def uninstall():
        sys.settrace = orig_settrace
        sys.setprofile = orig_setprofile
        threading.settrace = orig_threading_settrace
        sys.settrace(orig_trace)
        sys.setprofile(orig_profile)

    return uninstall


# ── weakref callback hooks ───────────────────────────────────

def install_weakref_hooks(system, wrap_callback):
    """Patch ``_weakref.ref`` to wrap callbacks when a context is active.

    When a weakly-referenced object is garbage-collected, its callback
    fires.  If a record/replay context is active, that callback must
    be wrapped so the system can record/replay synchronisation points
    around it.

    This function replaces ``_weakref.ref`` with a subclass whose
    ``__new__`` conditionally wraps the callback.  The condition is
    checked at the C level via ``functional.cond`` — no Python-level
    ``if`` on the hot path.

    Parameters
    ----------
    system : System
        Used to check gate state via ``_external.is_set`` and
        ``_internal.is_set``.
    wrap_callback : callable(callback) → callback
        Wraps a weakref callback for recording/replaying.
        Typically built with ``utils.observer`` and adapter hooks.

    Returns
    -------
    callable
        An uninstall function that restores the original ``_weakref.ref``.
    """
    import _weakref

    orig_ref = _weakref.ref
    orig_new = _weakref.ref.__new__

    def wrapping_new(cls, ob, callback=None, **kwargs):
        return orig_new(cls, ob, wrap_callback(callback) if callback else None, **kwargs)

    # Dispatch at C level: if either gate is active, wrap; else pass through.
    dispatched = functional.cond(
        system._external.is_set, wrapping_new,
        system._internal.is_set, wrapping_new,
        orig_new)

    new_ref = type('ref', (_weakref.ref,), {'__new__': dispatched})
    update(_weakref.ref, new_ref)

    def uninstall():
        update(new_ref, orig_ref)

    return uninstall


def init_weakref():
    """Force the weakref finalizer machinery to initialise.

    Creates and immediately detaches a dummy finalizer so that
    ``weakref.finalize``'s internal data structures are set up
    before the proxy system is active.  This avoids triggering
    the gates during the first real finalizer registration.
    """
    import weakref

    class _Dummy:
        pass

    f = weakref.finalize(_Dummy(), lambda: None)
    f.detach()
