"""
retracesoftware.utils - Runtime selectable release/debug builds

Set RETRACE_DEBUG=1 to use the debug build with symbols and assertions.
"""
import os
import sys

__path__ = [os.path.dirname(os.path.abspath(__file__))]
import warnings
from typing import Any
from types import ModuleType
import retracesoftware.functional as functional

def _is_truthy_env(v):
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}

_DEBUG_MODE = _is_truthy_env(os.getenv("RETRACE_DEBUG"))

_backend_mod: ModuleType
__backend__: str

try:
    if _DEBUG_MODE:
        import _retracesoftware_utils_debug as _backend_mod  # type: ignore
        __backend__ = "native-debug"
    else:
        import _retracesoftware_utils_release as _backend_mod  # type: ignore
        __backend__ = "native-release"
except Exception:
    raise ImportError("Failed to load retracesoftware native extensions")

# Expose debug mode flag
DEBUG_MODE = _DEBUG_MODE and __backend__.startswith("native")


_DEPRECATED = frozenset({
    "MemoryAddresses", "ThreadStatePredicate",
    "blocking_counter", "chain", "fastset", "has_generic_alloc",
    "has_generic_new", "hashseed", "id_dict", "idset", "instancecheck",
    "intercept__new__", "intercept_dict_set", "is_identity_hash",
    "is_immutable", "marker", "method_dispatch", "perthread", "reference",
    "return_none", "set_type", "start_new_thread_wrapper",
    "thread_switch_monitor", "unwrap_apply", "visitor",
    "yields_weakly_referenceable_instances",
})

_deprecated_local: dict = {}


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED:
        warnings.warn(
            f"retracesoftware.utils.{name} is deprecated and will be removed in a future release",
            DeprecationWarning,
            stacklevel=2,
        )
        if name in _deprecated_local:
            return _deprecated_local[name]
    return getattr(_backend_mod, name)


def _export_public(mod: ModuleType) -> None:
    g = globals()
    for k, v in mod.__dict__.items():
        if k.startswith("_") or k in _DEPRECATED:
            continue
        g[k] = v


_export_public(_backend_mod)

_NativeStackFactory = _backend_mod.StackFactory
stacktrace_exclude = set()


def exclude_from_stacktrace(function):
    stacktrace_exclude.add(function)
    return function


def StackFactory(*args, **kwargs):
    kwargs.setdefault("exclude", stacktrace_exclude.__contains__)
    return _NativeStackFactory(*args, **kwargs)

_WrappedBase = _backend_mod.Wrapped


class InternalWrapped(_WrappedBase):
    """Marker base for wrappers representing the internal domain."""


class ExternalWrapped(_WrappedBase):
    """Marker base for wrappers representing the external domain."""


# ---------------------------------------------------------------------------
# High-level API (convenience wrappers around C++ extension)
# ---------------------------------------------------------------------------

def wrap_func_with_overrides(func, **overrides):
    """
    Return a new function identical to `func` but with selected global names
    overridden by keyword arguments.
    """
    import builtins as _builtins
    import types

    orig = getattr(func, "__func__", func)
    g = dict(orig.__globals__)
    g.setdefault("__builtins__", _builtins.__dict__)
    g.update(overrides)

    return types.FunctionType(
        orig.__code__, g, orig.__name__, orig.__defaults__, orig.__closure__
    )


def patch_hashes(hashfunc, *types):
    """Patch ``__hash__`` on each type in *types* for deterministic ordering.

    Python's default ``__hash__`` is based on ``id()`` (memory address),
    which varies between runs.  Sets iterate in hash order, so iteration
    order becomes non-deterministic.  This replaces ``__hash__`` with
    *hashfunc*, giving stable set/dict-key ordering for record/replay.

    Call once during bootstrap, before any modules are loaded.
    """
    for cls in types:
        _backend_mod.patch_hash(cls, hashfunc)


def update(obj, name, f, *args, **kwargs):
    value = getattr(obj, name)
    setattr(obj, name, f(value, *args, **kwargs))


def throw(exc):
    raise exc


def _return_none(func):
    return functional.sequence(func, functional.constantly(None))

def _chain(*funcs):
    funcs = [f for f in funcs if f is not None]
    if not funcs:
        return None

    if len(funcs) == 1:
        return funcs[0]
    else:
        funcs = [_return_none(func) for func in funcs[:-1]] + [funcs[-1]]
        return functional.firstof(*funcs)

def runall(*funcs):
    funcs = tuple(func for func in funcs if func is not None)
    for func in funcs:
        if not callable(func):
            raise TypeError(f"runall item must be callable, got {func!r}")
    if not funcs:
        return noop
    return _backend_mod.runall(*funcs)

_deprecated_local["return_none"] = _return_none
_deprecated_local["chain"] = _chain


def thread_switch(function=None, *, on_thread_switch):
    """Compose ``thread_switch_monitor`` and ``observer``.

    This is a small Python convenience wrapper only. The hot path still uses
    the native ``thread_switch_monitor(...)`` and ``observer(...)`` helpers.
    """
    if not callable(on_thread_switch):
        raise TypeError(f"on_thread_switch must be callable, got {on_thread_switch!r}")

    monitor = _backend_mod.thread_switch_monitor(on_thread_switch=on_thread_switch)

    def decorate(target):
        if not callable(target):
            raise TypeError(f"function must be callable, got {target!r}")
        return _backend_mod.observer(target, on_call=monitor)

    if function is None:
        return decorate

    return decorate(function)

class Demultiplexer2:
    """Key-based demultiplexer wrapping Dispatcher.

    Provides the same interface as the C++ Demultiplexer but delegates
    to Dispatcher internally.

    Usage::

        demux = Demultiplexer2(source, key_function)
        item = demux(key)  # blocks until key_function(item) == key
    """

    def __init__(self, source, key_function, on_timeout=None, timeout_seconds=5):
        self._dispatcher = _backend_mod.Dispatcher(source)
        self._key_function = key_function
        self._on_timeout = on_timeout
        self._timeout_seconds = timeout_seconds
        self._pending_keys = set()

    def __call__(self, key):
        if key in self._pending_keys:
            raise ValueError(f"Key {key!r} already in set of pending gets")

        self._pending_keys.add(key)
        try:
            return self._dispatcher.next(
                lambda item, k=key: self._key_function(item) == k
            )
        except RuntimeError:
            if self._on_timeout:
                return self._on_timeout(self, key)
            raise
        finally:
            self._pending_keys.discard(key)

    @property
    def pending_keys(self):
        return tuple(self._pending_keys)

    def pending(self, key):
        item = self._dispatcher.buffered
        if self._key_function(item) != key:
            raise KeyError(key)
        return item

    @property
    def buffered(self):
        try:
            return self._dispatcher.buffered
        except Exception:
            return None

    @property
    def waiting_thread_count(self):
        return self._dispatcher.waiting_thread_count

    @property
    def source(self):
        return self._dispatcher.source

    def wait_for_all_pending(self):
        return self._dispatcher.wait_for_all_pending()

    def interrupt(self, on_waiting_thread, while_interrupted):
        return self._dispatcher.interrupt(on_waiting_thread, while_interrupted)


def gilwatch_library_path():
    """Return the absolute path to the gilwatch preload shared library, or None."""
    import pathlib
    ext = '.dylib' if sys.platform == 'darwin' else '.so'
    lib = pathlib.Path(__file__).parent.parent.parent / ('libgilwatch' + ext)
    return str(lib) if lib.exists() else None


def on_gilswitch(callback):
    """Register a callback invoked whenever the GIL changes thread.

    Requires libgilwatch to be preloaded (via DYLD_INSERT_LIBRARIES or
    LD_PRELOAD). Pass None to deactivate.
    """
    _backend_mod.gilwatch_activate(callback)


from retracesoftware.cursor import (
    CallCounter,
    Cursor,
    callback_on_thread,
    install_call_counter,
    uninstall_call_counter,
    current_call_counts,
    call_counter_disable_for,
    call_counter_frame_positions,
    call_counter_position,
    cursor_snapshot,
    yield_at_call_counts,
    yield_at_cursor,
    watch,
    install_cursor_hooks,
    uninstall_cursor_hooks,
    current_cursor,
    cursor_frame_positions,
    cursor_position,
    cursor_disable_for,
)

from .trace import trace_function_instructions, TargetUnreachableError, InstructionMonitor

__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
