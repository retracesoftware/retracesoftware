import gc
import os
import subprocess
import _socket
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
    assert deleted == [binding.handle]


def test_binder_rejects_non_weakrefable_object_without_bind_support():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        __slots__ = ()

    with pytest.raises(TypeError):
        binder.bind(Value())

    collect_garbage()
    assert deleted == []


def test_binder_lookup_does_not_unpack_tuple_after_weakref_fallback_initializes():
    binder = utils.Binder()

    with pytest.raises(TypeError):
        binder.bind("x")

    assert binder.lookup(("x", 1)) is None
    assert binder.lookup(("x", ("y", 2))) is None


def test_binder_callback_receives_binding_and_unbound_objects_do_not_emit_delete():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        pass

    bound_obj = Value()
    binding = binder.bind(bound_obj)
    del bound_obj
    collect_garbage()

    unbound_obj = Value()
    del unbound_obj
    collect_garbage()

    assert deleted == [binding.handle]
    assert isinstance(deleted[0], int)


def test_binder_emits_delete_for_bind_supported_non_weakrefable_object():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    utils.Binder.add_bind_support(_socket.socket)

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    binding = binder.bind(sock)

    sock.close()
    del sock
    collect_garbage()

    assert deleted == [binding.handle]


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

    assert deleted_left == [left_binding.handle]
    assert deleted_right == [right_binding.handle]


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

    assert deleted == [binding.handle]


def test_binder_swallows_callback_exceptions():
    binder = utils.Binder(on_delete=lambda binding: (_ for _ in ()).throw(RuntimeError("boom")))

    class Value:
        pass

    obj = Value()
    binder.bind(obj)

    del obj
    collect_garbage()


def test_system_patch_type_registers_bind_support(monkeypatch):
    from retracesoftware.proxy.system import System

    seen = []
    original = utils.Binder.add_bind_support

    def recording_add_bind_support(cls):
        seen.append(cls)
        return original(cls)

    monkeypatch.setattr(utils.Binder, "add_bind_support", staticmethod(recording_add_bind_support))

    system = System()
    try:
        system.patch_type(_socket.socket)
    finally:
        system.unpatch_types()

    assert _socket.socket in seen


@pytest.mark.parametrize("bind_order", ["base_first", "subtype_first"])
def test_binder_handles_subclass_then_base_tp_dealloc_chain_without_recursing(tmp_path, bind_order):
    script = tmp_path / "binder_socket_dealloc_repro.py"
    script.write_text(
        (
            "import _socket\n"
                "import gc\n"
                "import socket\n"
                "import retracesoftware.utils as utils\n"
                "\n"
                f"bind_order = {bind_order!r}\n"
                "utils.Binder.add_bind_support(_socket.socket)\n"
                "utils.Binder.add_bind_support(socket.socket)\n"
                "binder = utils.Binder()\n"
                "raw = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)\n"
                "fd = raw.detach()\n"
                "wrapped = socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM, 0, fd)\n"
            "if bind_order == 'base_first':\n"
            "    binder.bind(raw)\n"
            "    binder.bind(wrapped)\n"
            "else:\n"
            "    binder.bind(wrapped)\n"
            "    binder.bind(raw)\n"
            "wrapped.close()\n"
            "del wrapped\n"
            "gc.collect()\n"
            "print('ok')\n"
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in [
            env.get("PYTHONPATH", ""),
            str(Path(__file__).resolve().parents[2]),
            str(Path(__file__).resolve().parents[2] / "src"),
        ]
        if part
    )

    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_binder_remove_bind_support_disables_non_weakrefable_binding():
    binder = utils.Binder()

    utils.Binder.add_bind_support(_socket.socket)
    try:
        utils.Binder.remove_bind_support(_socket.socket)

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            with pytest.raises(TypeError):
                binder.bind(sock)
        finally:
            sock.close()
    finally:
        utils.Binder.add_bind_support(_socket.socket)
