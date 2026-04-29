import gc
import weakref

import retracesoftware.stream as stream


def collect_garbage():
    for _ in range(3):
        gc.collect()


def test_stream_binder_tracks_weakrefable_object_without_python_lookup_fallback():
    deleted = []
    binder = stream.Binder(on_delete=deleted.append)

    class Value:
        pass

    obj = Value()
    obj_ref = weakref.ref(obj)
    binding = binder.bind(obj)

    assert binder.lookup(obj) is binding
    assert binder.lookup(42) is None
    assert binder(42) == 42

    del obj
    collect_garbage()

    assert obj_ref() is None
    assert deleted == [binding.handle]


def test_stream_binder_weak_delete_callback_may_release_binder():
    deleted = []
    holder = {}

    def on_delete(handle):
        deleted.append(handle)
        holder.pop("binder", None)

    class Value:
        pass

    holder["binder"] = stream.Binder(on_delete=on_delete)
    obj = Value()
    obj_ref = weakref.ref(obj)
    binding = holder["binder"].bind(obj)
    handle = binding.handle

    del obj
    collect_garbage()

    assert obj_ref() is None
    assert deleted == [handle]
    assert "binder" not in holder
