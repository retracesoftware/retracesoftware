import gc
import sys
import weakref
from pathlib import Path

import pytest

_utils = pytest.importorskip("retracesoftware.utils")

try:
    import retracesoftware.utils as utils
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
    import utils  # type: ignore


def collect_garbage():
    for _ in range(3):
        gc.collect()


def test_binding_value_semantics():
    left = utils.Binding(7)
    same = utils.Binding(7)
    right = utils.Binding(8)

    assert left.handle == 7
    assert int(left) == 7
    assert left == same
    assert left != right
    assert hash(left) == hash(same)
    assert repr(left) == "Binding(7)"


def test_binder_bind_and_lookup_are_stable_for_same_object():
    binder = utils.Binder()

    class Value:
        pass

    obj = Value()

    first = binder.bind(obj)
    second = binder.bind(obj)
    looked_up = binder.lookup(obj)

    assert isinstance(first, utils.Binding)
    assert first is second
    assert first is looked_up
    assert binder.lookup(Value()) is None


def test_binder_emits_delete_for_weakrefable_object_without_keeping_it_alive():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        pass

    obj = Value()
    obj_ref = weakref.ref(obj)
    binding = binder.bind(obj)

    del obj
    collect_garbage()

    assert obj_ref() is None
    assert deleted == [binding]


def test_binder_emits_delete_for_non_weakrefable_object():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        __slots__ = ()

    obj = Value()
    binding = binder.bind(obj)

    del obj
    collect_garbage()

    assert deleted == [binding]


def test_binder_callback_receives_binding_and_unbound_objects_do_not_emit_delete():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        __slots__ = ()

    bound_obj = Value()
    binding = binder.bind(bound_obj)
    del bound_obj
    collect_garbage()

    unbound_obj = Value()
    del unbound_obj
    collect_garbage()

    assert deleted == [binding]
    assert isinstance(deleted[0], utils.Binding)


def test_multiple_binders_track_same_object_independently():
    deleted_left = []
    deleted_right = []
    left = utils.Binder(on_delete=deleted_left.append)
    right = utils.Binder(on_delete=deleted_right.append)

    class Value:
        pass

    obj = Value()
    left_binding = left.bind(obj)
    right_binding = right.bind(obj)

    assert left_binding != right_binding
    assert left.lookup(obj) is left_binding
    assert right.lookup(obj) is right_binding

    del obj
    collect_garbage()

    assert deleted_left == [left_binding]
    assert deleted_right == [right_binding]


def test_binder_on_delete_can_be_reassigned():
    deleted = []
    binder = utils.Binder()

    class Value:
        pass

    binder.on_delete = deleted.append
    obj = Value()
    binding = binder.bind(obj)

    del obj
    collect_garbage()

    assert deleted == [binding]


def test_binder_swallows_callback_exceptions():
    binder = utils.Binder(on_delete=lambda binding: (_ for _ in ()).throw(RuntimeError("boom")))

    class Value:
        pass

    obj = Value()
    binder.bind(obj)

    del obj
    collect_garbage()
