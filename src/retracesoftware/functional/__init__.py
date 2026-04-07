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
    "TypePredicate",
    "advice", "anyargs", "callall", "composeN", "deepwrap",
    "dropargs", "either", "first", "first_arg", "indexed", "instance_test",
    "method_invoker", "not_predicate", "notinstance_test",
    "selfapply", "ternary_predicate", "when_predicate",
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
    """when(test, then)(x) -> then(x) if test(x) else x."""
    ctor = getattr(_backend_mod, "when", None)
    if ctor is not None:
        return ctor(test, then)
    return _backend_mod.if_then_else(test, then, identity)


def when_not(test, then):
    """when_not(test, then)(x) -> x if test(x) else then(x)."""
    ctor = getattr(_backend_mod, "when_not", None)
    if ctor is not None:
        return ctor(test, then)

    def _gate(*args, **kwargs):
        if test(*args, **kwargs):
            return args[0] if args else None
        return then(*args, **kwargs)

    return _gate


def if_then_else(test, then, otherwise):
    """if_then_else(test, then, otherwise)(x) -> then(x) if test(x) else otherwise(x)."""
    if then is identity and otherwise is identity:
        return identity
    if otherwise is identity:
        return when(test, then)
    if then is identity:
        return when_not(test, otherwise)
    return _backend_mod.if_then_else(test, then, otherwise)


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
    """lazy(func, *args) -> compatibility wrapper for repeatedly(func, *args)."""
    return _backend_mod.repeatedly(func, *args)


def spread_and(pred):
    """spread_and(pred)(*args, **kwargs) -> True iff pred(value) is truthy for every arg and kwarg value."""
    ctor = getattr(_backend_mod, "spread_and", None)
    if ctor is not None:
        return ctor(pred)
    if not callable(pred):
        raise TypeError("spread_and() expects a callable")

    def _spread_and(*args, **kwargs):
        for value in args:
            if not pred(value):
                return False
        for value in kwargs.values():
            if not pred(value):
                return False
        return True

    return _spread_and


def spread_or(pred):
    """spread_or(pred)(*args, **kwargs) -> True iff pred(value) is truthy for any arg or kwarg value."""
    ctor = getattr(_backend_mod, "spread_or", None)
    if ctor is not None:
        return ctor(pred)
    if not callable(pred):
        raise TypeError("spread_or() expects a callable")

    def _spread_or(*args, **kwargs):
        for value in args:
            if pred(value):
                return True
        for value in kwargs.values():
            if pred(value):
                return True
        return False

    return _spread_or


def mapcall(function, *transforms):
    """mapcall(function, arg0tx, arg1tx, ..., resttx).

    If the trailing transform is ``identity``, drop it and use the backend's
    identity-rest fast path.
    """
    if not transforms:
        raise TypeError("mapcall() requires at least a rest transform")
    if transforms[-1] is identity:
        return _backend_mod.mapcall(function, *transforms[:-1], rest_is_identity=True)
    return _backend_mod.mapcall(function, *transforms)


def mapcall0(function, transform):
    """mapcall0(function, transform) == mapcall(function, transform, identity)."""
    return mapcall(function, transform, identity)


def isinstanceof(*classes, andnot=None):
    """isinstanceof(cls, andnot=None) or isinstanceof(cls1, cls2, ...).

    With one class, preserve the backend behavior. With multiple classes,
    return an ``or_predicate`` of the per-class predicates.
    """
    if not classes:
        raise TypeError("isinstanceof() requires at least one type")
    def _one(cls):
        if andnot is None:
            return _backend_mod.isinstanceof(cls)
        return _backend_mod.isinstanceof(cls, andnot=andnot)
    if len(classes) == 1:
        return _one(classes[0])
    return or_predicate(*(_one(cls) for cls in classes))


__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
