"""Regression coverage for patching built-in method descriptors."""

from __future__ import annotations

import _random
import types

from retracesoftware.proxy.system import System
from retracesoftware.proxy.patchtype import patch_type


def test_method_descriptor_unpatch_restores_original_descriptor() -> None:
    original = _random.Random.__dict__["getrandbits"]
    assert isinstance(original, types.MethodDescriptorType)

    system = System()
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
