import sys
import types
import posix

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch
from retracesoftware.install.replace import ModuleRefIndex, restore_module_refs
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System


def _wrap_marker(target):
    def wrapper(self):
        return ("wrapped", target(self))

    return wrapper


def _system():
    system = System(on_bind=utils.noop)
    system.primary_hooks = CallHooks()
    system.secondary_hooks = CallHooks()
    system.lifecycle_hooks = LifecycleHooks(on_start=utils.noop, on_end=utils.noop)
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    return system


def test_installation_proxy_replaces_function_and_uninstalls():
    system = _system()
    installation = Installation(system)

    calls = []

    def add(a, b):
        calls.append((a, b))
        return a + b

    namespace = {"__name__": "test_installation_proxy", "add": add}

    proxied = installation.proxy(namespace, "add")

    assert proxied is namespace["add"]
    assert proxied is not add
    assert installation.module_objects[0].name == "add"
    assert installation.module_objects[0].original is add
    assert installation.module_objects[0].current is proxied

    installation.uninstall()

    assert namespace["add"] is add


def test_installation_patch_type_unpatches_on_uninstall():
    system = _system()
    installation = Installation(system)

    class Example:
        def ping(self):
            return 123

    namespace = {"__name__": "test_installation_patch_type", "Example": Example}

    patched = installation.patch_type(namespace, "Example")

    assert patched is Example
    assert Example in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is system

    installation.uninstall()

    assert Example not in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is None


def test_installation_proxy_type_tracks_and_unpatches_type():
    system = _system()
    installation = Installation(system)

    class Example:
        def ping(self):
            return 123

    namespace = {"__name__": "test_installation_proxy_type", "Example": Example}

    proxied = installation.proxy(namespace, "Example")

    assert proxied is Example
    assert Example in system.patched_types
    assert installation.module_objects[0].name == "Example"

    installation.uninstall()

    assert Example not in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is None


def test_installation_can_update_module_refs_and_restore_them_on_uninstall():
    system = _system()
    installation = Installation(system, update_refs=True, module_refs_only=True)

    def add(a, b):
        return a + b

    source = types.ModuleType("test_installation_source")
    source.add = add

    peer = types.ModuleType("test_installation_peer")
    peer.exported = add

    sys.modules[source.__name__] = source
    sys.modules[peer.__name__] = peer

    try:
        proxied = installation.proxy(source, "add")

        assert source.add is proxied
        assert peer.exported is proxied

        installation.uninstall()

        assert source.add is add
        assert peer.exported is add
    finally:
        sys.modules.pop(source.__name__, None)
        sys.modules.pop(peer.__name__, None)


def test_module_ref_index_updates_aliases_across_sequential_replacements():
    def original():
        return "original"

    def first():
        return "first"

    def second():
        return "second"

    source = types.ModuleType("test_module_ref_index_source")
    source.target = original

    peer = types.ModuleType("test_module_ref_index_peer")
    peer.alias = original

    index = ModuleRefIndex([source, peer])

    source.target = first
    first_changes = index.replace(original, first)

    assert source.target is first
    assert peer.alias is first

    source.target = second
    second_changes = index.replace(first, second)

    assert source.target is second
    assert peer.alias is second

    restore_module_refs(second_changes)
    restore_module_refs(first_changes)

    assert source.target is second
    assert peer.alias is original


def test_module_ref_index_refreshes_modules_imported_after_construction():
    def original():
        return "original"

    def replacement():
        return "replacement"

    module = types.ModuleType("test_module_ref_index_late_module")
    module.alias = original

    index = ModuleRefIndex()
    sys.modules[module.__name__] = module
    try:
        changes = index.replace(original, replacement)

        assert module.alias is replacement

        restore_module_refs(changes)

        assert module.alias is original
    finally:
        sys.modules.pop(module.__name__, None)


def test_module_ref_index_global_scope_updates_class_attribute_caches():
    def original():
        return "original"

    def replacement():
        return "replacement"

    class CachedFactory:
        cached = original

    module = types.ModuleType("test_module_ref_index_class_attr_source")
    module.target = original

    index = ModuleRefIndex([module], global_scope=True)

    module.target = replacement
    changes = index.replace(original, replacement)

    try:
        assert module.target is replacement
        assert CachedFactory.cached is replacement
    finally:
        restore_module_refs(changes)

    assert module.target is replacement
    assert CachedFactory.cached is original


def test_installation_context_manager_uninstalls_on_exit():
    system = _system()

    def add(a, b):
        return a + b

    namespace = {"__name__": "test_installation_context_manager", "add": add}

    with Installation(system) as installation:
        proxied = installation.proxy(namespace, "add")
        assert namespace["add"] is proxied
        assert proxied is not add

    assert namespace["add"] is add


def test_patcher_patch_accepts_installation():
    system = _system()

    def add(a, b):
        return a + b

    namespace = {"__name__": "test_patcher_patch_accepts_installation", "add": add}

    undo = patch(namespace, {"disable": ["add"]}, Installation(system))

    assert namespace["add"] is not add

    undo()

    assert namespace["add"] is add


def test_type_attribute_proxy_preserves_method_binding_and_uninstalls():
    system = _system()

    class Example:
        def ping(self, value):
            return value + 1

    original = Example.__dict__["ping"]
    namespace = {"__name__": "test_type_attribute_proxy", "Example": Example}

    undo = patch(
        namespace,
        {"type_attributes": {"Example": {"proxy": ["ping"]}}},
        Installation(system),
    )

    try:
        assert Example().ping(41) == 42
    finally:
        undo()

    assert Example.__dict__["ping"] is original


def test_type_attribute_wrap_directive_applies_and_uninstalls():
    system = _system()

    class Example:
        def close(self):
            return "close"

    original_close = Example.__dict__["close"]
    namespace = {"__name__": "test_type_attribute_wrap", "Example": Example}

    undo = patch(
        namespace,
        {
            "type_attributes": {
                "Example": {
                    "wrap": {
                        "close": "tests.test_installation._wrap_marker",
                    },
                },
            },
        },
        Installation(system),
    )

    try:
        example = Example()
        assert example.close() == ("wrapped", "close")
    finally:
        undo()

    assert Example.__dict__["close"] is original_close


def test_pathparam_skips_callables_without_inspectable_signature():
    system = _system()
    target = functional.if_then_else(
        lambda path: True,
        lambda path: path,
        lambda path: path,
    )
    namespace = {"__name__": "test_pathparam_no_signature", "target": target}

    undo = patch(
        namespace,
        {"pathparam": {"target": "path"}},
        Installation(system),
        pathpredicate=lambda path: True,
    )

    try:
        assert namespace["target"] is target
    finally:
        undo()


def test_posix_pathparam_uses_first_arg_for_uninspectable_builtin(tmp_path):
    system = _system()
    seen = []
    path = tmp_path / "stamp.txt"
    path.write_text("ok", encoding="utf-8")
    namespace = {"__name__": "posix", "utime": posix.utime}

    undo = patch(
        namespace,
        {"pathparam": {"utime": "path"}},
        Installation(system),
        pathpredicate=lambda path: seen.append(path) or False,
    )

    try:
        namespace["utime"](str(path), None)
        assert seen == [str(path)]
    finally:
        undo()

    assert namespace["utime"] is posix.utime
