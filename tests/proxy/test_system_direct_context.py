import pytest

import retracesoftware.utils as utils
import retracesoftware.proxy.system as system_mod
from retracesoftware.install.patcher import patch as install_patch
from retracesoftware.proxy.messagestream import MemoryWriter
from retracesoftware.proxy.recorder import Recorder
from retracesoftware.proxy.replayer import Replayer
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System


_PATCHED_TYPE_KEEPALIVE = []


@pytest.fixture(autouse=True)
def keep_patched_test_types_alive(monkeypatch):
    """Prevent local patched class addresses from being reused across tests."""
    original = System.patch_type

    def patch_type_and_keepalive(self, cls, *args, **kwargs):
        _PATCHED_TYPE_KEEPALIVE.append(cls)
        return original(self, cls, *args, **kwargs)

    monkeypatch.setattr(System, "patch_type", patch_type_and_keepalive)


def _hooks():
    return dict(
        internal_hooks=CallHooks(),
        external_hooks=CallHooks(),
        lifecycle_hooks=LifecycleHooks(on_start=utils.noop, on_end=utils.noop),
        on_bind=utils.noop,
    )


def _restore_bound_snapshot(system, snapshot):
    keep_ids = {id(obj) for obj in snapshot}
    for obj in tuple(system.is_bound.ordered()):
        if id(obj) not in keep_ids:
            system.is_bound.discard(obj)


def test_system_context_patched_method_body_runs_outside_sandbox():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    seen = []

    class Example:
        def ping(self):
            seen.append((system._in_sandbox(), system._out_sandbox(), system.location))
            return 123

    system.patch_type(Example)

    with system.context(**_hooks()):
        assert system.location == "internal"
        assert Example().ping() == 123
        assert system.location == "internal"

    assert seen == [(False, True, "external")]


def test_system_context_callback_override_reenters_internal_sandbox():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    seen = []

    class Base:
        def trigger(self):
            return self.hook()

        def hook(self):
            return "base"

    system.patch_type(Base)

    class Sub(Base):
        def hook(self):
            seen.append((system._in_sandbox(), system._out_sandbox(), system.location))
            return "sub"

    with system.context(**_hooks()):
        assert Sub().trigger() == "sub"

    assert seen == [(True, True, "internal")]


def test_recorder_context_records_external_calls_through_system():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    writer = MemoryWriter()

    class Example:
        def ping(self):
            return 123

    system.patch_type(Example)

    with Recorder(system, writer).context():
        assert Example().ping() == 123

    assert "CALL" in writer.tape
    assert "RESULT" in writer.tape
    assert 123 in writer.tape


def test_dynamic_external_proxytype_record_emits_async_call_and_bind(monkeypatch):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    writer = MemoryWriter()
    monkeypatch.setattr(system_mod, "ExternalWrapped", utils.ExternalWrapped, raising=False)

    class External:
        def ping(self):
            return "pong"

    class Example:
        def make_external(self):
            return External()

    system.patch_type(Example)

    with Recorder(system, writer).context():
        root = Example()
        baseline = len(writer.tape)
        wrapped = root.make_external()

    assert isinstance(wrapped, utils.ExternalWrapped)

    delta = writer.tape[baseline:]
    assert "ASYNC_CALL" in delta

    async_index = delta.index("ASYNC_CALL")
    fn = delta[async_index + 1]
    args = delta[async_index + 2]
    kwargs = delta[async_index + 3]

    assert getattr(fn, "__name__", None) == "ext_proxytype_from_spec"
    assert args == ()
    assert kwargs["module"] == External.__module__
    assert kwargs["name"] == External.__qualname__
    assert "ping" in kwargs["methods"]

    binding_events = [item for item in delta if item.__class__.__name__ == "BindingCreate"]
    assert len(binding_events) == 1


def test_replayer_context_replays_external_calls_through_system():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    writer = MemoryWriter()

    calls = []

    class Example:
        def ping(self):
            calls.append("live")
            return len(calls)

    system.patch_type(Example)

    with Recorder(system, writer).context():
        assert Example().ping() == 1

    with Replayer(system, writer.reader()).context():
        assert Example().ping() == 1

    assert calls == ["live"]


def test_dynamic_external_proxytype_replay_runs_async_call_and_binds_new_type(monkeypatch):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    writer = MemoryWriter()
    monkeypatch.setattr(system_mod, "ExternalWrapped", utils.ExternalWrapped, raising=False)

    class External:
        def ping(self):
            return "pong"

    class Example:
        def make_external(self):
            return External()

    system.patch_type(Example)

    with Recorder(system, writer).context():
        recorded_root = Example()
        recorded = recorded_root.make_external()

    baseline_bound = tuple(system.is_bound.ordered())
    dynamic_types = [
        obj
        for obj in baseline_bound
        if isinstance(obj, type) and getattr(obj, "__name__", None) == External.__qualname__
    ]
    for obj in dynamic_types:
        system.is_bound.discard(obj)

    with Replayer(system, writer.reader()).context():
        replay_root = Example()
        replayed = replay_root.make_external()

    rebound_types = [
        obj
        for obj in system.is_bound.ordered()
        if isinstance(obj, type) and getattr(obj, "__name__", None) == External.__qualname__
    ]

    assert isinstance(recorded, utils.ExternalWrapped)
    assert isinstance(replayed, utils.ExternalWrapped)
    assert rebound_types


def test_installed_system_can_record_then_replay_against_same_tape_after_bound_reset():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    live_calls = []

    class Service:
        def ping(self):
            live_calls.append("live")
            return len(live_calls)

    module_namespace = {
        "__name__": "test_installed_service",
        "Service": Service,
    }
    uninstall = install_patch(
        module_namespace,
        {"proxy": ["Service"]},
        system,
    )

    try:
        InstalledService = module_namespace["Service"]
        baseline_bound = tuple(system.is_bound.ordered())
        writer = MemoryWriter()

        with Recorder(system, writer).context():
            recorded_service = InstalledService()
            assert system.is_bound(recorded_service)
            recorded = recorded_service.ping()

        assert recorded == 1
        assert live_calls == ["live"]
        assert writer.tape, "recording should produce an in-memory tape"

        _restore_bound_snapshot(system, baseline_bound)
        assert not system.is_bound(recorded_service)

        with Replayer(system, writer.reader()).context():
            replayed_service = InstalledService()
            assert system.is_bound(replayed_service)
            replayed = replayed_service.ping()

        assert replayed == recorded
        assert live_calls == ["live"]
    finally:
        uninstall()


def test_installed_system_replays_external_phase_allocation_via_async_callback_protocol():
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    live_calls = []

    class Service:
        def make_peer(self):
            live_calls.append("make_peer")
            return type(self)()

        def ping(self):
            live_calls.append("ping")
            return "pong"

    module_namespace = {
        "__name__": "test_installed_service_async_new",
        "Service": Service,
    }
    uninstall = install_patch(
        module_namespace,
        {"proxy": ["Service"]},
        system,
    )

    try:
        InstalledService = module_namespace["Service"]
        baseline_bound = tuple(system.is_bound.ordered())
        writer = MemoryWriter()

        with Recorder(system, writer).context():
            root = InstalledService()
            peer = root.make_peer()
            assert peer is not root
            assert system.is_bound(peer)
            assert peer.ping() == "pong"

        assert "ASYNC_NEW_PATCHED" not in writer.tape
        assert "ASYNC_CALL" in writer.tape
        assert live_calls == ["make_peer", "ping"]

        _restore_bound_snapshot(system, baseline_bound)
        assert not system.is_bound(root)
        assert not system.is_bound(peer)

        with Replayer(system, writer.reader()).context():
            replay_root = InstalledService()
            replay_peer = replay_root.make_peer()
            assert replay_peer is not replay_root
            assert system.is_bound(replay_peer)
            assert replay_peer.ping() == "pong"

        assert live_calls == ["make_peer", "ping"]
    finally:
        uninstall()
