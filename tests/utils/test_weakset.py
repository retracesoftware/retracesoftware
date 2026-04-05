import gc

import pytest

utils = pytest.importorskip("retracesoftware.utils")


def test_weakset_weakrefable_objects_auto_evict():
    class Weakrefable:
        pass

    weakset = utils.WeakSet()
    obj = Weakrefable()

    assert weakset(obj) is False
    assert weakset.add(obj) is True
    assert weakset(obj) is True
    assert len(weakset) == 1

    del obj
    gc.collect()

    assert len(weakset) == 0


def test_weakset_non_weakrefable_objects_fallback_to_strong_refs():
    class NonWeakrefable:
        __slots__ = ()

    weakset = utils.WeakSet()
    obj = NonWeakrefable()

    assert weakset.add(obj) is True
    assert weakset(obj) is True
    assert len(weakset) == 1

    del obj
    gc.collect()

    assert len(weakset) == 1


def test_weakset_ordered_snapshot_contains_live_mixed_entries():
    class Weakrefable:
        pass

    class NonWeakrefable:
        __slots__ = ()

    weak = Weakrefable()
    strong = NonWeakrefable()
    weak2 = Weakrefable()

    weakset = utils.WeakSet()
    assert weakset.add(weak) is True
    assert weakset.add(strong) is True
    assert weakset.add(weak2) is True

    assert set(weakset.ordered()) == {weak, strong, weak2}


def test_weakset_ordered_snapshot_skips_evicted_entries():
    class Weakrefable:
        pass

    first = Weakrefable()
    second = Weakrefable()
    third = Weakrefable()

    weakset = utils.WeakSet()
    assert weakset.add(first) is True
    assert weakset.add(second) is True
    assert weakset.add(third) is True

    del second
    gc.collect()

    assert set(weakset.ordered()) == {first, third}
