"""Regression coverage for patching built-in method descriptors."""

from __future__ import annotations

import _random
import random
import types

from retracesoftware.proxy.patchtype import patch_type
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System


def _system() -> System:
    system = System()
    system.primary_hooks = CallHooks()
    system.secondary_hooks = CallHooks()
    system.lifecycle_hooks = LifecycleHooks(on_start=lambda: None, on_end=lambda: None)
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    return system


def test_method_descriptor_unpatch_restores_original_descriptor() -> None:
    original = _random.Random.__dict__["getrandbits"]
    assert isinstance(original, types.MethodDescriptorType)

    system = _system()
    try:
        patch_type(system, _random.Random)
        assert _random.Random.__dict__["getrandbits"] is not original

        system.unpatch_type(_random.Random)
        assert _random.Random.__dict__["getrandbits"] is original

        patch_type(system, _random.Random)
        assert _random.Random.__dict__["getrandbits"] is not original
    finally:
        system.unpatch_types()

    assert _random.Random.__dict__["getrandbits"] is original


def test_method_descriptor_accepts_direct_random_subclass_receiver() -> None:
    class LocalRandom(_random.Random):
        pass

    system = _system()
    original = _random.Random.__dict__["getrandbits"]

    try:
        patch_type(system, _random.Random)

        rng = LocalRandom()
        assert isinstance(rng, _random.Random)
        assert isinstance(rng, LocalRandom)
        assert isinstance(system.run(rng.getrandbits, 8), int)
    finally:
        system.unpatch_types()

    assert _random.Random.__dict__["getrandbits"] is original


def test_method_descriptor_accepts_python_random_subclass_receiver() -> None:
    class LocalRandom(random.Random):
        pass

    system = _system()
    original = _random.Random.__dict__["getrandbits"]

    try:
        patch_type(system, _random.Random)

        rng = LocalRandom()
        assert isinstance(rng, _random.Random)
        assert isinstance(rng, random.Random)
        assert isinstance(system.run(rng.getrandbits, 8), int)
    finally:
        system.unpatch_types()

    assert _random.Random.__dict__["getrandbits"] is original
