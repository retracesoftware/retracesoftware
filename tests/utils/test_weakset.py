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
