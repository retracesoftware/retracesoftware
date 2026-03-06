"""
Pure-Python fallback implementation for `retracesoftware.functional`.

This is intentionally focused on correctness and portability (matching the
public API exercised by the test suite), not performance.
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Sequence, Tuple


def identity(obj: Any) -> Any:
    """identity(obj) -> obj. Return `obj` unchanged."""

    return obj


def typeof(obj: Any) -> type:
    """typeof(obj) -> type. Return the exact type of `obj`."""

    return type(obj)


def apply(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """apply(func, *args, **kwargs) -> func(*args, **kwargs)."""

    return func(*args, **kwargs)


def first_arg(*args: Any, **kwargs: Any) -> Any:
    """first_arg(*args, **kwargs) -> first positional arg."""

    if not args:
        raise TypeError("first_arg() requires at least one positional argument")
    return args[0]


def compose(f: Callable[[Any], Any], g: Callable[..., Any]) -> Callable[..., Any]:
    """compose(f, g)(*args, **kwargs) == f(g(*args, **kwargs))."""

    if not callable(f) or not callable(g):
        raise TypeError("compose() expects callables")

    @functools.wraps(f)
    def _composed(*args: Any, **kwargs: Any) -> Any:
        return f(g(*args, **kwargs))

    return _composed


def composeN(*funcs: Any) -> Callable[..., Any]:
    """
    composeN(f1, f2, f3)(x) == f3(f2(f1(x))).

    If passed a single list/tuple, it is treated as the function sequence.

    Alias: `sequence`
    """

    if len(funcs) == 1 and isinstance(funcs[0], (list, tuple)):
        funcs = tuple(funcs[0])

    if not funcs:
        raise TypeError("composeN() requires at least one function")

    for f in funcs:
        if not callable(f):
            raise TypeError("composeN() expects callables")

    def _composed(*args: Any, **kwargs: Any) -> Any:
        v = funcs[0](*args, **kwargs)
        for f in funcs[1:]:
            v = f(v)
        return v

    return _composed


# Alias for composeN (left-to-right composition)
sequence = composeN


def callall(funcs: Iterable[Callable[..., Any]]) -> Callable[..., Any]:
    """callall(funcs)(*args, **kwargs) calls each function in order; returns last result (or None)."""

    funcs = tuple(funcs)
    for f in funcs:
        if not callable(f):
            raise TypeError("callall() expects callables")

    def _callall(*args: Any, **kwargs: Any) -> Any:
        result = None
        for f in funcs:
            result = f(*args, **kwargs)
        return result

    return _callall


def juxt(*funcs: Callable[..., Any]) -> Callable[..., Tuple[Any, ...]]:
    """juxt(f1, f2, ...)(*args, **kwargs) -> (f1(...), f2(...), ...)"""

    for f in funcs:
        if not callable(f):
            raise TypeError("juxt() expects callables")

    def _juxt(*args: Any, **kwargs: Any) -> Tuple[Any, ...]:
        return tuple(f(*args, **kwargs) for f in funcs)

    return _juxt


def use_with(target: Callable[..., Any], *transforms: Callable[..., Any]) -> Callable[..., Any]:
    """use_with(target, t1, t2)(*args, **kwargs) -> target(t1(*args, **kwargs), t2(*args, **kwargs))"""

    if not callable(target):
        raise TypeError("use_with() expects a callable target")
    for t in transforms:
        if not callable(t):
            raise TypeError("use_with() expects callable transforms")

    def _use(*args: Any, **kwargs: Any) -> Any:
        vals = [t(*args, **kwargs) for t in transforms]
        return target(*vals)

    return _use


def and_predicate(*preds: Callable[..., Any]) -> Callable[..., bool]:
    """and_predicate(p1, p2, ...)(x) -> True iff all predicates are truthy (short-circuits)."""

    for p in preds:
        if not callable(p):
            raise TypeError("and_predicate() expects callables")

    def _and(*args: Any, **kwargs: Any) -> bool:
        for p in preds:
            if not p(*args, **kwargs):
                return False
        return True

    return _and


def or_predicate(*preds: Callable[..., Any]) -> Callable[..., bool]:
    """or_predicate(p1, p2, ...)(x) -> True iff any predicate is truthy (short-circuits)."""

    for p in preds:
        if not callable(p):
            raise TypeError("or_predicate() expects callables")

    def _or(*args: Any, **kwargs: Any) -> bool:
        for p in preds:
            if p(*args, **kwargs):
                return True
        return False

    return _or


def not_predicate(pred: Callable[..., Any]) -> Callable[..., bool]:
    """not_predicate(pred)(x) -> not pred(x)."""

    if not callable(pred):
        raise TypeError("not_predicate() expects a callable")

    def _not(*args: Any, **kwargs: Any) -> bool:
        return not bool(pred(*args, **kwargs))

    return _not


class TypePredicate:
    """TypePredicate(cls)(obj) -> True iff type(obj) is exactly cls (no subclass matching)."""

    def __init__(self, cls: type):
        if not isinstance(cls, type):
            raise TypeError("TypePredicate expects a type")
        self.cls = cls

    def __call__(self, obj: Any) -> bool:
        return type(obj) is self.cls


def ternary_predicate(
    condition: Callable[..., Any], on_true: Callable[..., Any], on_false: Callable[..., Any]
) -> Callable[..., Any]:
    """ternary_predicate(cond, on_true, on_false)(*args, **kwargs) dispatches based on cond(*args, **kwargs)."""

    if not callable(condition) or not callable(on_true) or not callable(on_false):
        raise TypeError("ternary_predicate() expects callables")

    def _ternary(*args: Any, **kwargs: Any) -> Any:
        return on_true(*args, **kwargs) if condition(*args, **kwargs) else on_false(*args, **kwargs)

    return _ternary


def if_then_else(
    condition: Callable[..., Any], then_fn: Callable[..., Any] | None, else_fn: Callable[..., Any] | None
) -> Callable[..., Any]:
    """if_then_else(cond, then, else)(*args, **kwargs) -> then(...) if cond(...) else else(...)."""

    if not callable(condition):
        raise TypeError("if_then_else() expects a callable condition")
    if then_fn is not None and not callable(then_fn):
        raise TypeError("if_then_else() expects then_fn to be callable or None")
    if else_fn is not None and not callable(else_fn):
        raise TypeError("if_then_else() expects else_fn to be callable or None")

    def _gate(*args: Any, **kwargs: Any) -> Any:
        if condition(*args, **kwargs):
            return None if then_fn is None else then_fn(*args, **kwargs)
        return None if else_fn is None else else_fn(*args, **kwargs)

    return _gate


def when_predicate(pred: Callable[..., Any], then: Callable[..., Any]) -> Callable[..., Any]:
    """when_predicate(pred, then)(*args, **kwargs) -> then(...) if pred(...) else None."""

    if not callable(pred) or not callable(then):
        raise TypeError("when_predicate() expects callables")

    def _when(*args: Any, **kwargs: Any) -> Any:
        return then(*args, **kwargs) if pred(*args, **kwargs) else None

    return _when


def isinstanceof(cls: type, andnot: type | None = None) -> Callable[[Any], bool]:
    """isinstanceof(cls, andnot=None)(obj) -> isinstance(obj, cls) and not isinstance(obj, andnot)."""

    if not isinstance(cls, type):
        raise TypeError("isinstanceof(cls) requires cls to be a type")
    if andnot is not None and not isinstance(andnot, type):
        raise TypeError("isinstanceof(andnot=...) requires a type or None")

    def _pred(obj: Any) -> bool:
        ok = isinstance(obj, cls)
        if andnot is not None:
            ok = ok and (not isinstance(obj, andnot))
        return ok

    return _pred


def instance_test(cls: type) -> Callable[[Any], Any]:
    """instance_test(cls)(obj) -> obj if isinstance(obj, cls) else None."""

    if not isinstance(cls, type):
        raise TypeError("instance_test must be passed a type")

    def _test(obj: Any) -> Any:
        return obj if isinstance(obj, cls) else None

    return _test


def notinstance_test(cls: type) -> Callable[[Any], Any]:
    """notinstance_test(cls)(obj) -> obj if NOT isinstance(obj, cls) else None."""

    if not isinstance(cls, type):
        raise TypeError("notinstance_test must be passed a type")

    def _test(obj: Any) -> Any:
        return obj if not isinstance(obj, cls) else None

    return _test


def dispatch(*parts: Any) -> Callable[..., Any]:
    """
    dispatch(test1, then1, test2, then2, ..., [otherwise]) -> callable.

    If the argument count is odd, the last argument is treated as the fallback handler.
    """

    if len(parts) < 2:
        raise TypeError("dispatch() requires at least (test, then)")

    fallback = None
    if len(parts) % 2 == 1:
        fallback = parts[-1]
        parts = parts[:-1]
        if not callable(fallback):
            raise TypeError("dispatch() fallback must be callable")

    pairs = list(zip(parts[0::2], parts[1::2]))
    for p, h in pairs:
        if not callable(p) or not callable(h):
            raise TypeError("dispatch() expects callable predicate/handler pairs")

    def _dispatch(*args: Any, **kwargs: Any) -> Any:
        for p, h in pairs:
            if p(*args, **kwargs):
                return h(*args, **kwargs)
        return None if fallback is None else fallback(*args, **kwargs)

    return _dispatch


def first(*functions: Callable[..., Any]) -> Callable[..., Any]:
    """first(f1, f2, ...)(*args, **kwargs) returns the first non-None result (short-circuits)."""

    for f in functions:
        if not callable(f):
            raise TypeError("first() expects callables")

    def _first(*args: Any, **kwargs: Any) -> Any:
        for f in functions:
            r = f(*args, **kwargs)
            if r is not None:
                return r
        return None

    return _first


def firstof(*functions: Callable[..., Any]) -> Callable[..., Any]:
    """
    firstof(f1, f2, ..., last)(*args, **kwargs)
    - returns first non-None from f1..f(n-1)
    - always calls `last` as fallback if none matched earlier
    """

    if not functions:
        raise TypeError("firstof() requires at least one function")
    for f in functions:
        if not callable(f):
            raise TypeError("firstof() expects callables")

    *front, last = functions

    def _firstof(*args: Any, **kwargs: Any) -> Any:
        for f in front:
            r = f(*args, **kwargs)
            if r is not None:
                return r
        return last(*args, **kwargs)

    return _firstof


def partial(func: Callable[..., Any], *pargs: Any, required: int | None = None, **pkwargs: Any) -> Callable[..., Any]:
    """partial(func, *args, required=None, **kwargs) -> callable (pure-Python fallback)."""

    if not callable(func):
        raise TypeError("partial() expects a callable")

    if required is not None and required != 0:
        # The native implementation supports richer semantics; this fallback only implements what tests cover.
        raise NotImplementedError("pure partial() only supports required=None or required=0")

    if required == 0:
        def _thunk(*args: Any, **kwargs: Any) -> Any:
            return func(*pargs, **pkwargs)

        return _thunk

    def _p(*args: Any, **kwargs: Any) -> Any:
        merged = dict(pkwargs)
        merged.update(kwargs)
        return func(*pargs, *args, **merged)

    return _p


def always(value: Any) -> Callable[..., Any]:
    """always(x) returns a callable that ignores its args; if x is callable it is invoked with no args."""

    if callable(value):
        def _always_callable(*args: Any, **kwargs: Any) -> Any:
            return value()

        return _always_callable

    def _always_value(*args: Any, **kwargs: Any) -> Any:
        return value

    return _always_value


def repeatedly(func: Callable[[], Any]) -> Callable[..., Any]:
    """repeatedly(func) calls func() every time, ignoring all passed arguments."""

    if not callable(func):
        raise TypeError("repeatedly() expects a callable")

    def _rep(*args: Any, **kwargs: Any) -> Any:
        return func()

    return _rep


def constantly(value: Any) -> Callable[..., Any]:
    """constantly(x) returns a callable that always returns x (never calls x even if callable)."""

    def _const(*args: Any, **kwargs: Any) -> Any:
        return value

    return _const


def anyargs(func: Callable[[], Any]) -> Callable[..., Any]:
    """anyargs(func) wraps a no-arg function so it can be called with any args/kwargs (ignored)."""

    if not callable(func):
        raise TypeError("anyargs() expects a callable")

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        return func()

    return _wrapped


def selfapply(factory: Callable[..., Callable[..., Any]]) -> Callable[..., Any]:
    """selfapply(factory)(*args, **kwargs) == factory(*args, **kwargs)(*args, **kwargs)."""

    if not callable(factory):
        raise TypeError("selfapply() expects a callable")

    def _selfapply(*args: Any, **kwargs: Any) -> Any:
        inner = factory(*args, **kwargs)
        return inner(*args, **kwargs)

    return _selfapply


def spread(target: Callable[..., Any], *transforms: Callable[[Any], Any] | None) -> Callable[[Any], Any]:
    """spread(target, t1, t2)(x) -> target(t1(x), t2(x)); None transform means pass x unchanged."""

    if not callable(target):
        raise TypeError("spread() expects a callable target")
    for t in transforms:
        if t is not None and not callable(t):
            raise TypeError("spread() expects transforms to be callable or None")

    def _spread(x: Any) -> Any:
        vals = [(x if t is None else t(x)) for t in transforms]
        return target(*vals)

    return _spread


def dropargs(func: Callable[..., Any], n: int = 1) -> Callable[..., Any]:
    """dropargs(func, n=1)(*args, **kwargs) calls func(*args[n:], **kwargs)."""

    if not callable(func):
        raise TypeError("dropargs() expects a callable")
    if not isinstance(n, int) or n < 0:
        raise TypeError("dropargs() expects n to be a non-negative int")

    def _dropped(*args: Any, **kwargs: Any) -> Any:
        return func(*args[n:], **kwargs)

    return _dropped


def indexed(i: int) -> Callable[[Sequence[Any]], Any]:
    """indexed(i)(seq) -> seq[i]."""

    if not isinstance(i, int):
        raise TypeError("indexed() expects an int")

    def _idx(seq: Sequence[Any]) -> Any:
        return seq[i]

    return _idx


def param(name: str, index: int) -> Callable[..., Any]:
    """param(name, index)(*args, **kwargs) prefers kwargs[name], else args[index], else ValueError."""

    if not isinstance(name, str):
        raise TypeError("param() expects name to be a str")
    if not isinstance(index, int):
        raise TypeError("param() expects index to be an int")

    def _param(*args: Any, **kwargs: Any) -> Any:
        if name in kwargs:
            return kwargs[name]
        if 0 <= index < len(args):
            return args[index]
        raise ValueError(f"Missing parameter '{name}'")

    return _param


class positional_param:
    """positional_param(index)(*args) -> args[index]. Ignores kwargs."""

    __slots__ = ('index',)

    def __init__(self, index: int):
        if not isinstance(index, int) or index < 0:
            raise ValueError("positional_param index must be a non-negative int")
        self.index = index

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.index < len(args):
            return args[self.index]
        raise IndexError(
            f"positional_param({self.index}): expected at least "
            f"{self.index + 1} positional args, got {len(args)}")

    def __repr__(self) -> str:
        return f"positional_param({self.index})"


class _MapArgs:
    def __init__(self, func: Callable[..., Any], transform: Callable[[Any], Any], starting: int = 0):
        self._func = func
        self._transform = transform
        self._starting = starting

        functools.update_wrapper(self, func)  # type: ignore[arg-type]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        args2 = list(args)
        for i in range(self._starting, len(args2)):
            args2[i] = self._transform(args2[i])

        kwargs2 = {k: self._transform(v) for k, v in kwargs.items()}
        return self._func(*args2, **kwargs2)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._func, name)


def mapargs(func: Callable[..., Any], transform: Callable[[Any], Any], starting: int = 0) -> Callable[..., Any]:
    """mapargs(func, transform, starting=0) applies transform to args[starting:] and all kwarg values."""

    if not callable(func) or not callable(transform):
        raise TypeError("mapargs() expects callables")
    if not isinstance(starting, int) or starting < 0:
        raise TypeError("mapargs() expects starting to be a non-negative int")
    return _MapArgs(func, transform, starting=starting)


def advice(
    target: Callable[..., Any],
    *,
    on_call: Callable[..., Any] | None = None,
    on_result: Callable[[Any], Any] | None = None,
    on_error: Callable[[type, BaseException, Any], Any] | None = None,
) -> Callable[..., Any]:
    """advice(target, on_call=None, on_result=None, on_error=None) wraps calls with AOP-style hooks."""

    if not callable(target):
        raise TypeError("advice() expects a callable target")
    if on_call is not None and not callable(on_call):
        raise TypeError("on_call must be callable or None")
    if on_result is not None and not callable(on_result):
        raise TypeError("on_result must be callable or None")
    if on_error is not None and not callable(on_error):
        raise TypeError("on_error must be callable or None")

    @functools.wraps(target)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        if on_call is not None:
            on_call(*args, **kwargs)
        try:
            r = target(*args, **kwargs)
        except BaseException:
            if on_error is not None:
                exc_type, exc_value, exc_tb = sys.exc_info()
                assert exc_type is not None and exc_value is not None
                on_error(exc_type, exc_value, exc_tb)
            raise
        if on_result is not None:
            on_result(r)
        return r

    return _wrapped


def intercept(
    target: Callable[..., Any],
    *,
    on_call: Callable[..., Any] | None = None,
    on_result: Callable[[Any], Any] | None = None,
    on_error: Callable[[type, BaseException, Any], Any] | None = None,
) -> Callable[..., Any]:
    """intercept(...) is currently equivalent to advice(...) in the pure-Python fallback."""

    return advice(target, on_call=on_call, on_result=on_result, on_error=on_error)


def side_effect(effect: Callable[..., Any]) -> Callable[..., Any]:
    """side_effect(effect)(x, *args, **kwargs) runs effect(...) then returns the original first arg."""

    if not callable(effect):
        raise TypeError("side_effect() expects a callable")

    def _side(*args: Any, **kwargs: Any) -> Any:
        effect(*args, **kwargs)
        return args[0] if args else None

    return _side


def method_invoker(obj: Any, method_name: str, lookup_error: BaseException | None = None) -> Callable[..., Any]:
    """method_invoker(obj, name)(*args, **kwargs) calls getattr(obj, name)(*args, **kwargs)."""

    if not isinstance(method_name, str):
        raise TypeError("method_invoker() expects method_name to be a str")

    def _invoke(*args: Any, **kwargs: Any) -> Any:
        try:
            m = getattr(obj, method_name)
        except AttributeError:
            if lookup_error is not None:
                raise lookup_error
            raise
        return m(*args, **kwargs)

    return _invoke


def memoize_one_arg(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """memoize_one_arg(func)(x) caches results by object identity (id(x))."""

    if not callable(func):
        raise TypeError("memoize_one_arg() expects a callable")

    cache: Dict[int, Any] = {}
    keepalive: Dict[int, Any] = {}

    @functools.wraps(func)
    def _memo(x: Any) -> Any:
        k = id(x)
        if k in cache:
            return cache[k]
        r = func(x)
        cache[k] = r
        keepalive[k] = x
        return r

    return _memo


def when_not_none(func: Callable[..., Any]) -> Callable[..., Any]:
    """when_not_none(func)(*args, **kwargs) returns None without calling func if any arg/kwarg is None."""

    if not callable(func):
        raise TypeError("when_not_none() expects a callable")

    @functools.wraps(func)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        for a in args:
            if a is None:
                return None
        for v in kwargs.values():
            if v is None:
                return None
        return func(*args, **kwargs)

    return _wrapped


def either(first_fn: Callable[..., Any], second_fn: Callable[..., Any]) -> Callable[..., Any]:
    """either(f, g)(*args, **kwargs) returns f(...) if not None, else g(...)."""

    if not callable(first_fn) or not callable(second_fn):
        raise TypeError("either() expects callables")

    def _either(*args: Any, **kwargs: Any) -> Any:
        r = first_fn(*args, **kwargs)
        if r is not None:
            return r
        return second_fn(*args, **kwargs)

    return _either


def walker(transform: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """walker(transform)(obj) recursively applies transform to leaf values of tuples/lists/dicts."""

    if not callable(transform):
        raise TypeError("walker() expects a callable")

    def _walk(obj: Any) -> Any:
        if isinstance(obj, tuple):
            changed = False
            out = []
            for x in obj:
                y = _walk(x)
                changed = changed or (y is not x)
                out.append(y)
            return tuple(out) if changed else obj
        if isinstance(obj, list):
            changed = False
            out = []
            for x in obj:
                y = _walk(x)
                changed = changed or (y is not x)
                out.append(y)
            return out if changed else obj
        if isinstance(obj, dict):
            changed = False
            out: Dict[Any, Any] = {}
            for k, v in obj.items():
                v2 = _walk(v)
                changed = changed or (v2 is not v)
                out[k] = v2
            return out if changed else obj
        return transform(obj)

    return _walk


def deepwrap(wrapper: Callable[[Any], Any], func: Callable[..., Any]) -> Callable[..., Any]:
    """deepwrap(wrapper, func) wraps func's result and recursively wraps callable results."""

    if not callable(wrapper) or not callable(func):
        raise TypeError("deepwrap() expects callables")

    @functools.wraps(func)
    def _deep(*args: Any, **kwargs: Any) -> Any:
        r = func(*args, **kwargs)
        r2 = wrapper(r)
        if callable(r2):
            return deepwrap(wrapper, r2)
        return r2

    return _deep


__all__ = [
    "TypePredicate",
    "advice",
    "always",
    "and_predicate",
    "anyargs",
    "apply",
    "callall",
    "compose",
    "composeN",
    "constantly",
    "deepwrap",
    "dispatch",
    "dropargs",
    "either",
    "first",
    "first_arg",
    "firstof",
    "identity",
    "if_then_else",
    "indexed",
    "instance_test",
    "intercept",
    "isinstanceof",
    "mapargs",
    "memoize_one_arg",
    "method_invoker",
    "not_predicate",
    "notinstance_test",
    "or_predicate",
    "param",
    "positional_param",
    "partial",
    "repeatedly",
    "selfapply",
    "sequence",
    "side_effect",
    "spread",
    "ternary_predicate",
    "typeof",
    "use_with",
    "walker",
    "when_not_none",
    "when_predicate",
]

