import gc
import os
import subprocess
import _thread
import _socket
import sys
import weakref
from pathlib import Path

import pytest

stream = pytest.importorskip("retracesoftware.stream")

try:
    import retracesoftware.stream as utils
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
    import stream as utils  # type: ignore


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


def test_binder_bind_raises_for_same_object():
    binder = utils.Binder()

    class Value:
        pass

    obj = Value()

    first = binder.bind(obj)

    assert isinstance(first, utils.Binding)
    with pytest.raises(RuntimeError, match="already bound"):
        binder.bind(obj)

    looked_up = binder.lookup(obj)
    assert first is looked_up
    assert binder.lookup(Value()) is None


def test_binder_uses_identity_for_weakrefable_equal_objects():
    binder = utils.Binder()

    class Value:
        def __init__(self, token):
            self.token = token

        def __eq__(self, other):
            return isinstance(other, Value) and self.token == other.token

        def __hash__(self):
            return hash(self.token)

    left = Value("same")
    right = Value("same")

    left_binding = binder.bind(left)
    assert binder.lookup(left) is left_binding
    assert binder.lookup(right) is None

    right_binding = binder.bind(right)
    assert right_binding is not left_binding
    assert binder.lookup(right) is right_binding


def test_binder_uses_identity_for_equal_builtin_functions():
    binder = utils.Binder()

    left_binding = binder.bind(_thread.allocate)

    assert _thread.allocate == _thread.allocate_lock
    assert _thread.allocate is not _thread.allocate_lock
    assert binder.lookup(_thread.allocate_lock) is None

    right_binding = binder.bind(_thread.allocate_lock)
    assert right_binding is not left_binding
    assert binder.lookup(_thread.allocate) is left_binding
    assert binder.lookup(_thread.allocate_lock) is right_binding


def test_binder_callable_returns_argument_unchanged_when_unbound():
    binder = utils.Binder()

    class Value:
        pass

    obj = Value()

    assert binder(obj) is obj


def test_binder_callable_returns_binding_when_bound():
    binder = utils.Binder()

    class Value:
        pass

    obj = Value()
    binding = binder.bind(obj)
    looked_up = binder(obj)

    assert isinstance(looked_up, utils.Binding)
    assert looked_up.handle == binding.handle


def test_binder_callable_requires_exactly_one_positional_argument():
    binder = utils.Binder()

    with pytest.raises(TypeError):
        binder()

    with pytest.raises(TypeError):
        binder(object(), object())

    with pytest.raises(TypeError):
        binder(obj=object())


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


def test_binder_falls_back_for_non_weakrefable_object_without_bind_support():
    deleted = []
    binder = utils.Binder(on_delete=deleted.append)

    class Value:
        __slots__ = ()

    obj = Value()
    binding = binder.bind(obj)

    assert isinstance(binding, utils.Binding)
    assert binder.lookup(obj) is binding

    del obj
    collect_garbage()
    assert deleted == []


def test_binder_lookup_does_not_unpack_tuple_after_weakref_fallback_initializes():
    binder = utils.Binder()

    binding = binder.bind("x")

    assert isinstance(binding, utils.Binding)
    assert binder.lookup("x") is binding
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
    from retracesoftware.proxy.patchtype import patch_type
    from retracesoftware.proxy.system import System

    seen = []
    original = utils.Binder.add_bind_support

    def recording_add_bind_support(cls):
        seen.append(cls)
        return original(cls)

    monkeypatch.setattr(utils.Binder, "add_bind_support", staticmethod(recording_add_bind_support))

    system = System()
    try:
        patch_type(system, _socket.socket)
    finally:
        system.unpatch_types()

    assert _socket.socket in seen


def test_binder_composes_with_later_on_alloc_dealloc_wrapper(tmp_path):
    script = tmp_path / "binder_on_alloc_dealloc_stack.py"
    script.write_text(
        (
            "import gc\n"
            "import retracesoftware.stream as stream\n"
            "import retracesoftware.utils as runtime_utils\n"
            "\n"
            "class Value:\n"
            "    __slots__ = ()\n"
            "\n"
            "stream.Binder.add_bind_support(Value)\n"
            "binder = stream.Binder()\n"
            "\n"
            "# Patch binder first so set_on_alloc captures binder_dealloc as the\n"
            "# existing dealloc wrapper, matching the replay socket failure mode.\n"
            "warmup = Value()\n"
            "binder.bind(warmup)\n"
            "del warmup\n"
            "gc.collect()\n"
            "\n"
            "runtime_utils.set_on_alloc(Value, lambda obj: None)\n"
            "\n"
            "obj = Value()\n"
            "binder.bind(obj)\n"
            "del obj\n"
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


@pytest.mark.parametrize("bind_order", ["base_first", "subtype_first"])
def test_binder_handles_subclass_then_base_tp_dealloc_chain_without_recursing(tmp_path, bind_order):
    script = tmp_path / "binder_socket_dealloc_repro.py"
    script.write_text(
        (
            "import _socket\n"
                "import gc\n"
                "import socket\n"
                "import retracesoftware.stream as utils\n"
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


def test_binder_remove_bind_support_falls_back_to_generic_binding():
    binder = utils.Binder()

    utils.Binder.add_bind_support(_socket.socket)
    try:
        utils.Binder.remove_bind_support(_socket.socket)

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            binding = binder.bind(sock)
            assert isinstance(binding, utils.Binding)
            assert binder.lookup(sock) is binding
        finally:
            sock.close()
    finally:
        utils.Binder.add_bind_support(_socket.socket)
