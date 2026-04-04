"""Helpers for preparing protocol payloads for checkpoint writes/comparison."""

from __future__ import annotations

import enum
import types


def normalize(value):
    """Reduce arbitrary values to a stable, tape-safe checkpoint form."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value
    if isinstance(value, int):
        # Large integers are often memory addresses or pointer-like ids.
        if value > 1000000 or value < -1000000:
            return "XXXX"
        return int(value)
    if isinstance(value, float):
        return value
    if isinstance(value, types.FunctionType):
        return value.__qualname__
    if isinstance(value, types.MethodType):
        return value.__qualname__
    if isinstance(value, types.BuiltinFunctionType):
        return value.__name__
    if isinstance(value, types.BuiltinMethodType):
        return value.__name__
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, dict):
        return {
            normalize(key): normalize(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(normalize(item) for item in value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return f"<object of type: {type(value)}>"


__all__ = ["normalize"]
