import sys
import types

import retracesoftware.utils as utils

from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System


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
