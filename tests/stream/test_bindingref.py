"""Regression tests for native binding reference marker types."""

import pytest


pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


@pytest.mark.parametrize(
    ("binding_type", "type_name"),
    [
        (stream.BindingCreate, "BindingCreate"),
        (stream.BindingLookup, "BindingLookup"),
        (stream.BindingDelete, "BindingDelete"),
    ],
)
@pytest.mark.parametrize("index", [0, 1, 24, 299, 304, 322])
def test_binding_ref_constructor_preserves_index(binding_type, type_name, index):
    ref = binding_type(index)

    assert ref.index == index
    assert repr(ref) == f"{type_name}(index={index})"
