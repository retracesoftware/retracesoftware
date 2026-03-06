"""Thread-spawn middleware system.

Middleware is registered via ``add_thread_middleware(factory)``.  Each
*factory* is called in the **parent** thread just before
``_thread.start_new_thread`` and must return a context manager (or
``None`` to skip).  The context manager wraps the child thread's
execution::

    factory(fn, args, kwargs) -> context_manager | None

``_thread.start_new_thread`` (and ``threading._start_new_thread``)
are patched at import time with a C-level dispatcher via
``functional.partial``.  The patch adds no Python frames to either
the parent or child stack.

This module re-exports the API for convenience.
"""
from retracesoftware.utils import add_thread_middleware

__all__ = ["add_thread_middleware"]
