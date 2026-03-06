"""
retracesoftware.utils - Runtime selectable release/debug builds

Set RETRACE_DEBUG=1 to use the debug build with symbols and assertions.
"""
import os
import sys

__path__ = [os.path.dirname(os.path.abspath(__file__))]
import warnings
import weakref
import threading
from collections import UserDict
from dataclasses import dataclass
from typing import Any
from types import ModuleType
import retracesoftware.functional as functional
import _thread
import threading as _threading

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
    raise ImportError("Failed to load retracesoftware_utils native extension")

# Expose debug mode flag
DEBUG_MODE = _DEBUG_MODE and __backend__.startswith("native")


_DEPRECATED = frozenset({
    "MemoryAddresses", "Proxy", "ThreadStatePredicate",
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


_thread_middleware = []

_thread.start_new_thread = _threading._start_new_thread = functional.partial(
    _backend_mod.start_new_thread_wrapper,
    _thread.start_new_thread,
    _thread_middleware)

def add_thread_middleware(factory):
    """Register a thread-spawn middleware factory.

    *factory()* is called in the parent thread (with no arguments) and
    must return a context manager (or None to skip).  The CM wraps the
    child thread's execution.

    Returns a callable that removes this middleware when called.
    """
    func = functional.repeatedly(factory)
    _thread_middleware.append(func)

    def remove():
        try:
            _thread_middleware.remove(func)
        except ValueError:
            pass

    return remove

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

_deprecated_local["return_none"] = _return_none
_deprecated_local["chain"] = _chain

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

    @property
    def pending(self):
        try:
            return self._dispatcher.buffered
        except RuntimeError:
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


# ---------------------------------------------------------------------------
# CallCounter — C extension type for per-frame call-count tracking
# ---------------------------------------------------------------------------

CallCounter = _backend_mod.CallCounter

_default_call_counter = None

def _get_default_call_counter():
    global _default_call_counter
    if _default_call_counter is None:
        _default_call_counter = CallCounter()
    return _default_call_counter

def install_call_counter():
    """Install per-thread call-count tracking hooks."""
    _get_default_call_counter().install()

def uninstall_call_counter():
    """Remove call-count tracking hooks and reset the stack."""
    _get_default_call_counter().uninstall()

def current_call_counts():
    """Return the current call counts as a tuple of ints."""
    return _get_default_call_counter().current()

def call_counter_frame_positions():
    """Return a tuple of f_lasti ints aligned to the call-count stack."""
    return _get_default_call_counter().frame_positions()

def call_counter_reset():
    """Clear the call-count stack."""
    _get_default_call_counter().reset()

def call_counter_position():
    """Return (call_count, f_lasti) pairs for every frame on the stack."""
    return _get_default_call_counter().position()

def yield_at_call_counts(callback, thread_id, call_counts):
    """Arm a one-shot callback for a target thread/call-counts."""
    _get_default_call_counter().yield_at(callback, thread_id, call_counts)

def call_counter_disable_for(fn):
    """Return a C wrapper that freezes call-count tracking for the duration of fn."""
    return _get_default_call_counter().disable_for(fn)

def watch(thread_id, call_counts, *, on_start=None, on_return=None,
          on_unwind=None, on_backjump=None):
    """Arm one-shot callbacks for a target thread/call-counts position.

    Each provided callback fires once when the matching event occurs for
    the given call-count path on the specified thread.

    ``on_start``    — function entry (PY_START)
    ``on_return``   — normal return (PY_RETURN)
    ``on_unwind``   — exception unwind (PY_UNWIND)
    ``on_backjump`` — backward jump, e.g. loop iteration (JUMP)
    """
    _get_default_call_counter().watch(
        thread_id, call_counts,
        on_start=on_start, on_return=on_return,
        on_unwind=on_unwind, on_backjump=on_backjump,
    )

# ---------------------------------------------------------------------------
# Cursor — immutable data type representing a position in a recorded trace
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cursor:
    """A position in a recorded trace.

    ``thread_id``: the OS thread that was executing.
    ``function_counts``: per-frame call counts from root to leaf.
    ``f_lasti``: bytecode offset of the top frame, or None for function entry.
    """
    thread_id: int
    function_counts: tuple
    f_lasti: int | None = None

    def to_dict(self) -> dict:
        d: dict = {"thread_id": self.thread_id, "function_counts": list(self.function_counts)}
        if self.f_lasti is not None:
            d["f_lasti"] = self.f_lasti
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Cursor":
        return cls(
            thread_id=d["thread_id"],
            function_counts=tuple(d["function_counts"]),
            f_lasti=d.get("f_lasti"),
        )

def cursor_snapshot() -> Cursor:
    """Take a snapshot of the current execution position as a Cursor."""
    counts = current_call_counts()
    positions = call_counter_frame_positions()
    return Cursor(
        thread_id=_thread.get_ident(),
        function_counts=counts,
        f_lasti=positions[-1] if positions else None,
    )


def _yield_at_cursor_impl(thread_id, counters, f_lasti, callback):
    cc = _get_default_call_counter()

    if f_lasti is None:
        cc.watch(thread_id, counters, on_start=callback)
        return

    def _phase2():
        frame = sys._getframe(1)
        code = frame.f_code
        tool_id = cc.tool_id
        target = f_lasti

        if frame.f_lasti >= target:
            callback()
            return

        def _on_instruction(code_obj, offset):
            if offset == target:
                sys.monitoring.set_local_events(tool_id, code, 0)
                callback()
            return sys.monitoring.DISABLE

        sys.monitoring.register_callback(
            tool_id, sys.monitoring.events.INSTRUCTION,
            cc.disable_for(_on_instruction),
        )
        sys.monitoring.set_local_events(
            tool_id, code, sys.monitoring.events.INSTRUCTION,
        )

    wrapped = cc.disable_for(_phase2)
    cc.watch(thread_id, counters, on_return=wrapped, on_unwind=wrapped)


_yield_at_cursor_wrapped = None

def yield_at_cursor(thread_id, counters, f_lasti, callback):
    """Yield at a precise cursor position using a two-phase approach.

    If *f_lasti* is ``None``, arms *callback* via ``on_start`` for
    *counters* directly (the function-entry case).

    Otherwise uses *counters* to identify a child function invocation and
    *f_lasti* as the bytecode offset in the **parent frame** where we
    need to stop:

    Phase 1 — ``watch(on_return/on_unwind)`` waits for the child at
    *counters* to finish so the parent frame becomes active.

    Phase 2 — enables per-code-object ``INSTRUCTION`` monitoring on the
    parent frame and fires *callback* when ``instruction_offset == f_lasti``.

    The function itself is wrapped with ``disable_for`` so it does not
    perturb the call-count stack.
    """
    global _yield_at_cursor_wrapped
    if _yield_at_cursor_wrapped is None:
        _yield_at_cursor_wrapped = call_counter_disable_for(_yield_at_cursor_impl)
    _yield_at_cursor_wrapped(thread_id, counters, f_lasti, callback)


# Backward-compat aliases (do NOT override the Cursor dataclass above)
install_cursor_hooks = install_call_counter
uninstall_cursor_hooks = uninstall_call_counter
current_cursor = current_call_counts
cursor_frame_positions = call_counter_frame_positions
cursor_reset = call_counter_reset
cursor_position = call_counter_position
cursor_disable_for = call_counter_disable_for


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


from .trace import trace_function_instructions, TargetUnreachableError, InstructionMonitor

__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
