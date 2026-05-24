import pytest

from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import ReplayStubCallError, patch


class _PatchSystem:
    def __init__(self, mode):
        self.retrace_mode = mode
        self.proxied_types = []

    def proxy_type(self, cls):
        self.proxied_types.append(cls)
        return cls

    def patch_function(self, fn):
        return fn


def test_stub_for_replay_replaces_type_only_during_replay():
    class NativeHandle:
        @property
        def value(self):
            return "live"

        def poll(self):
            return "live"

    namespace = {"__name__": "demo_native", "NativeHandle": NativeHandle}

    record_system = _PatchSystem("record")
    undo_record = patch(
        namespace,
        {"stub_for_replay": ["NativeHandle"]},
        Installation(record_system),
    )
    try:
        assert namespace["NativeHandle"] is NativeHandle
    finally:
        undo_record()

    replay_system = _PatchSystem("replay")
    undo_replay = patch(
        namespace,
        {"stub_for_replay": ["NativeHandle"]},
        Installation(replay_system),
    )
    try:
        StubHandle = namespace["NativeHandle"]
        instance = StubHandle("ignored", keyword=True)

        assert StubHandle is not NativeHandle
        assert StubHandle.__module__ == NativeHandle.__module__
        assert StubHandle.__name__ == NativeHandle.__name__
        assert StubHandle.__qualname__ == NativeHandle.__qualname__
        assert isinstance(instance, StubHandle)

        class ChildHandle(StubHandle):
            pass

        assert issubclass(ChildHandle, StubHandle)

        with pytest.raises(ReplayStubCallError, match="poll"):
            instance.poll()
        with pytest.raises(ReplayStubCallError, match="value"):
            instance.value
    finally:
        undo_replay()


def test_stub_for_replay_runs_before_proxy_directive():
    class NativeHandle:
        def poll(self):
            return "live"

    namespace = {"__name__": "demo_native", "NativeHandle": NativeHandle}
    replay_system = _PatchSystem("replay")
    proxied_types = []

    def capture_proxy_type(obj):
        proxied_types.append(obj)
        return obj

    replay_system.proxy_type = capture_proxy_type
    undo = patch(
        namespace,
        {"stub_for_replay": ["NativeHandle"], "proxy": ["NativeHandle"]},
        Installation(replay_system),
    )
    try:
        StubHandle = namespace["NativeHandle"]
        assert StubHandle is not NativeHandle
        assert proxied_types == [StubHandle]
    finally:
        undo()


def test_stub_for_replay_proxy_patches_generated_shape():
    class NativeHandle:
        @property
        def value(self):
            return "live"

        def poll(self):
            return "live"

    namespace = {"__name__": "demo_native", "NativeHandle": NativeHandle}
    replay_system = _PatchSystem("replay")
    undo = patch(
        namespace,
        {"stub_for_replay": ["NativeHandle"], "proxy": ["NativeHandle"]},
        Installation(replay_system),
    )
    try:
        StubHandle = namespace["NativeHandle"]
        assert StubHandle is not NativeHandle
        assert StubHandle in replay_system.proxied_types
    finally:
        undo()
