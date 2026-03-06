"""
`retracesoftware.functional` can run in three modes:

- Native Release (C++ extension): fast, optimized, preferred for production.
- Native Debug (C++ extension): includes debug symbols and assertions, for debugging.
- Pure Python: slower, but works on platforms where the extension cannot be loaded.

Set `RETRACE_DEBUG=1` to use the debug build instead of release.
Set `RETRACESOFTWARE_FUNCTIONAL_PURE_PYTHON=1` (or `FUNCTIONAL_PURE_PYTHON=1`)
to force the pure-Python backend even if the native extension is available.
"""

from __future__ import annotations

import os
import warnings
from types import ModuleType
from typing import Any


def _is_truthy_env(v: str | None) -> bool:
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


_FORCE_PURE = _is_truthy_env(os.getenv("RETRACESOFTWARE_FUNCTIONAL_PURE_PYTHON")) or _is_truthy_env(
    os.getenv("FUNCTIONAL_PURE_PYTHON")
)

_DEBUG_MODE = _is_truthy_env(os.getenv("RETRACE_DEBUG"))

_backend_mod: ModuleType
__backend__: str

if not _FORCE_PURE:
    try:
        if _DEBUG_MODE:
            # Debug build with symbols and assertions
            import _retracesoftware_functional_debug as _backend_mod  # type: ignore
            __backend__ = "native-debug"
        else:
            # Release build (optimized)
            import _retracesoftware_functional_release as _backend_mod  # type: ignore
            __backend__ = "native-release"
    except Exception:  # ImportError/OSError are the common cases; keep this broad for platform loader quirks.
        from . import _pure as _backend_mod
        __backend__ = "pure"
else:
    from . import _pure as _backend_mod
    __backend__ = "pure"

# Expose debug mode flag
DEBUG_MODE = _DEBUG_MODE and __backend__.startswith("native")


_DEPRECATED = frozenset({
    "TypePredicate", "positional_param",
    "advice", "anyargs", "callall", "composeN", "deepwrap",
    "dropargs", "either", "first", "first_arg", "indexed", "instance_test",
    "method_invoker", "not_predicate", "notinstance_test",
    "selfapply", "ternary_predicate", "use_with", "when_predicate",
})


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED:
        warnings.warn(
            f"retracesoftware.functional.{name} is deprecated and will be removed in a future release",
            DeprecationWarning,
            stacklevel=2,
        )
    return getattr(_backend_mod, name)


def _export_public(mod: ModuleType) -> None:
    g = globals()
    for k, v in mod.__dict__.items():
        if k.startswith("_") or k in _DEPRECATED:
            continue
        g[k] = v


_export_public(_backend_mod)

# ---------------------------------------------------------------------------
# Convenience functions (originally from src/functional.py)
# ---------------------------------------------------------------------------

def sequence(*args):
    """Compose functions left-to-right: sequence(f, g, h)(x) == h(g(f(x)))."""
    if len(args) == 0:
        raise Exception("sequence requires at least one argument")
    elif len(args) == 1:
        if args[0] is None:
            return sequence()
        else:
            return args[0]
    elif len(args) == 2:
        if args[0] is None:
            return sequence(args[1])
        elif args[1] is None:
            return sequence(args[0])
        else:
            # sequence(g, f) => f(g(x))
            return _backend_mod.compose(args[1], args[0])
    else:
        if args[-1] is None:
            return sequence(*args[:-1])
        else:
            return _backend_mod.compose(args[-1], sequence(*args[:-1]))


def when(test, then):
    """when(test, then)(x) -> then(x) if test(x) else None."""
    return _backend_mod.if_then_else(test, then, None)


def cond(*args):
    """
    Build a chain of if_then_else: cond(cond1, action1, cond2, action2, ..., default).
    Returns a callable that evaluates the first matching condition and applies its action.
    If no condition matches, returns the result of the default (callable or constant).
    """
    if len(args) < 1:
        raise ValueError("cond requires at least one argument (the default)")
    if len(args) % 2 != 1:
        raise ValueError("cond requires an odd number of args: cond1, action1, cond2, action2, ..., default")

    default = args[-1]
    if len(args) == 1:
        return default if callable(default) else _backend_mod.constantly(default)

    result = default if callable(default) else _backend_mod.constantly(default)
    n = (len(args) - 1) // 2
    for i in range(n - 1, -1, -1):
        c, a = args[2 * i], args[2 * i + 1]
        result = _backend_mod.if_then_else(c, a, result)
    return result


def lazy(func, *args):
    """lazy(func, *args) -> a thunk that calls func(*args) when invoked (ignores call-time args)."""
    return _backend_mod.partial(func, *args, required=0)


__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
