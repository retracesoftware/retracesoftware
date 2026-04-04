"""Helpers for deterministic bind-sequence checkpoints."""

import retracesoftware.utils as utils


def _callable_name(target):
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", getattr(target, "__name__", None))
    objclass = getattr(target, "__objclass__", None)

    if qualname is None:
        qualname = type(target).__qualname__

    if objclass is not None and "." not in qualname:
        qualname = f"{objclass.__qualname__}.{qualname}"

    return f"{module}.{qualname}" if module else qualname


def binding_name(obj):
    if isinstance(obj, utils.wrapped_function):
        return f"wrapped:{_callable_name(utils.try_unwrap(obj))}"

    if isinstance(obj, type):
        return f"type:{obj.__module__}.{obj.__qualname__}"

    unwrapped = utils.try_unwrap(obj)
    target_type = getattr(type(obj), "__retrace_target_type__", None) or type(unwrapped)
    return f"object:{target_type.__module__}.{target_type.__qualname__}"


def checkpoint_bind(checkpoint, ledger, obj):
    if checkpoint is None:
        return None

    ledger.append(binding_name(obj))
    checkpoint(("bindings", tuple(ledger)))
    return None
