import types

import retracesoftware.gateway._dynamicproxy as dynamicproxy
import retracesoftware.gateway._gatewaypair as gatewaypair_module
import retracesoftware.proxy.system2 as system2_module
import retracesoftware.stream as stream
import retracesoftware.utils as utils
import pytest

from retracesoftware.gateway._dynamicproxy import ProxyRef
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CheckpointMessage,
    DefaultTraceWriter,
    ErrorMessage,
    ResultMessage,
    ThreadSwitchMessage,
)
from retracesoftware.install import ReplayDivergence
from retracesoftware.proxy.system2 import ReplayThreadScheduleError, System2


def test_thread_cursors_advance_returns_full_cursor():
    cursors = system2_module._ThreadCursors()

    assert cursors.advance("main", (0, 1, 2)) == (1, 2)
    assert cursors.advance("main", (1, 7)) == (1, 7)
    assert cursors.advance("worker", (0, 3)) == (3,)


class _FakeSpace:
    current = None
    _next_id = 1

    def __init__(self):
        self.id = _FakeSpace._next_id
        _FakeSpace._next_id += 1
        self.thread_switch = lambda previous_delta, next_thread_id: None
        self.call_at_calls = []
        self.thread_delta_value = (0,)
        self.coordinates_value = ()

    @property
    def apply(self):
        def apply(function, *args, **kwargs):
            previous = _FakeSpace.current
            _FakeSpace.current = self
            try:
                return function(*args, **kwargs)
            finally:
                _FakeSpace.current = previous

        return apply

    def wrap(self, function):
        def wrapped(*args, **kwargs):
            return self.apply(function, *args, **kwargs)

        return wrapped

    def call_at(self, *args):
        self.call_at_calls.append(args)

    def thread_delta(self):
        return self.thread_delta_value

    def coordinates(self):
        return self.coordinates_value


class _FakeSpaceDispatch:
    def __init__(self, default, cases=()):
        self.default = default
        self.mapping = {}
        for space, function in cases:
            self[space] = function

    def _key(self, space):
        return space.id if hasattr(space, "id") else space

    def __setitem__(self, space, function):
        self.mapping[self._key(space)] = function

    def __call__(self, *args, **kwargs):
        space = _FakeSpace.current
        key = space.id if space is not None else None
        return self.mapping.get(key, self.default)(*args, **kwargs)


class _FakeHandoff:
    def __init__(self):
        self.to_calls = []

    def to(self, thread_id):
        self.to_calls.append(thread_id)


class _FakeWriter:
    def __init__(self):
        self.calls = []

    def callback(self, fn, args, kwargs):
        self.calls.append(("callback", fn, args, kwargs))

    def error(self, error):
        self.calls.append(("error", error))

    def result(self, value):
        self.calls.append(("result", value))

    def thread_switch(self, cursor_delta, thread_id):
        self.calls.append(("thread_switch", cursor_delta, thread_id))

    def checkpoint(self, cursor_delta, value):
        self.calls.append(("checkpoint", cursor_delta, value))

    def new_binding(self, handle):
        self.calls.append(("new_binding", handle))

    def binding_delete(self, handle):
        self.calls.append(("binding_delete", handle))


class _FakeReader:
    def __init__(self, messages):
        self.messages = list(messages)

    def __call__(self):
        return self.messages.pop(0)


def _fake_retrace():
    _FakeSpace.current = None
    _FakeSpace._next_id = 1
    return types.SimpleNamespace(
        CoordinateSpace=_FakeSpace,
        ThreadHandoff=_FakeHandoff,
        root_space=_FakeSpace(),
        space_dispatch=lambda default, cases=(): _FakeSpaceDispatch(default, cases),
    )


def _tagged_proxy(label):
    def proxy(_proxytype_from):
        def wrap(value):
            return (label, value)

        return wrap

    return proxy


def _install_fake_retrace(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(system2_module, "retrace", fake_retrace)
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    return fake_retrace


def test_record_system_creates_gateway_pair_and_type_patcher(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()

    system = System2.record_system(writer)

    assert system.gateway_pair is not None
    assert system.type_patcher.gateway_pair is system.gateway_pair
    assert system.patched_types is system.type_patcher.patched_types


def test_record_system_external_call_writes_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)
    writer.calls.clear()

    result = system.gateway_pair.external(lambda value: f"result:{value}", "x")

    assert result == ("wrapped", "result:('wrapped', 'x')")
    assert writer.calls[-1] == ("result", ("wrapped", "result:('wrapped', 'x')"))


def test_record_system_callback_writes_callback(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)
    writer.calls.clear()

    def callback(value):
        return f"callback:{value}"

    assert system.gateway_pair.internal(callback, "x") == (
        "wrapped",
        "callback:('wrapped', 'x')",
    )
    assert ("callback", callback, (("wrapped", "x"),), {}) in writer.calls


def test_record_system_bind_writes_new_binding_and_encodes_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)

    class Bound:
        pass

    obj = Bound()
    system.bind(obj)
    writer.calls.clear()

    system.gateway_pair.external(lambda: obj)

    assert len(writer.calls) == 1
    event, value = writer.calls[0]
    assert event == "result"
    assert isinstance(value, stream.Binding)


def test_record_system_passes_dynamic_external_proxy_to_writer(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)

    class ExternalProxy(utils.ExternalWrapped):
        pass

    proxy = ProxyRef(ExternalProxy)()
    writer.calls.clear()

    system.gateway_pair.external(lambda: proxy)

    assert writer.calls == [("result", proxy)]


def test_record_system_writes_bound_patched_object_as_binding(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)

    class Patched:
        pass

    unpatch = system.patch_type(Patched)
    try:
        obj = Patched()
        system.bind(obj)
        writer.calls.clear()

        system.gateway_pair.external(lambda: obj)

        assert len(writer.calls) == 1
        event, value = writer.calls[0]
        assert event == "result"
        assert isinstance(value, stream.Binding)
    finally:
        unpatch()


def test_record_system_checkpoint_writes_cursor_delta_and_encoded_value(monkeypatch):
    _install_fake_retrace(monkeypatch)
    trace = []
    system = System2.record_system(DefaultTraceWriter(trace.append))
    system.internal_space.thread_delta_value = (1, 2)

    system.checkpoint({"state": "ok"})

    checkpoints = [
        message for message in trace
        if isinstance(message, CheckpointMessage)
    ]
    assert len(checkpoints) == 1
    assert checkpoints[0].cursor_delta == (1, 2)
    assert checkpoints[0].value == {"state": "ok"}


def test_replay_system_resolves_bound_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    obj = object()
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        BindOpenMessage(1),
        ResultMessage(stream.Binding(1)),
    ]))
    system.bind(obj)

    def live_target():
        raise AssertionError("live target should not run")

    assert system.gateway_pair.external(live_target) is obj


def test_replay_system_checkpoint_accepts_matching_value(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        CheckpointMessage((0, 1, 2), {"state": "ok"}),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    system.checkpoint({"state": "ok"})


def test_replay_system_checkpoint_raises_on_value_difference(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        CheckpointMessage((0, 1, 2), {"state": "record"}),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    with pytest.raises(ReplayDivergence, match="checkpoint difference"):
        system.checkpoint({"state": "replay"})


def test_replay_system_checkpoint_raises_on_cursor_difference(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        CheckpointMessage((0, 1, 2), {"state": "ok"}),
    ]))
    system.internal_space.coordinates_value = (1, 3)

    with pytest.raises(ReplayDivergence, match="checkpoint cursor difference"):
        system.checkpoint({"state": "ok"})


def test_replay_system_consumes_binding_delete_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    obj = object()
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        BindOpenMessage(1),
        BindCloseMessage(1),
        ResultMessage("ok"),
    ]))
    system.bind(obj)

    assert system.gateway_pair.external(lambda: "live") == "ok"


def test_replay_system_schedules_thread_switch_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system2_module._thread, "get_ident", lambda: 1)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        ThreadSwitchMessage((0, 3, 5), thread_id="worker"),
        ResultMessage("ok"),
    ]))

    assert system.gateway_pair.external(lambda: "live") == "ok"

    assert len(system.internal_space.call_at_calls) == 1
    thread_id, cursor, on_hit, on_missed = system.internal_space.call_at_calls[0]
    assert thread_id == 1
    assert cursor == (3, 5)
    assert callable(on_hit)
    assert callable(on_missed)
    assert system.handoff.to_calls == []
    on_hit()
    assert system.handoff.to_calls == ["worker"]
    with pytest.raises(ReplayThreadScheduleError):
        on_missed()


def test_replay_system_schedules_thread_switch_delta_from_current_thread(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system2_module._thread, "get_ident", lambda: 1)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        ThreadSwitchMessage((0, 1, 2), thread_id="worker"),
        ThreadSwitchMessage((1, 7), thread_id="main"),
        ResultMessage("ok"),
    ]))

    assert system.gateway_pair.external(lambda: "live") == "ok"

    first, second = system.internal_space.call_at_calls
    assert first[0] == 1
    assert first[1] == (1, 2)
    assert second[0] == 1
    assert second[1] == (1, 7)
    assert system.handoff.to_calls == []
    first[2]()
    second[2]()
    assert system.handoff.to_calls == ["worker", "main"]


def test_recorded_thread_switch_replays_as_scheduled_handoff(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system2_module._thread, "get_ident", lambda: 1)
    trace = []
    record = System2.record_system(DefaultTraceWriter(trace.append))

    record.internal_space.thread_switch((0, 3), "worker")
    assert record.gateway_pair.external(lambda: "recorded") == ("wrapped", "recorded")

    switches = [message for message in trace if isinstance(message, ThreadSwitchMessage)]
    assert len(switches) == 1
    assert switches[0].cursor_delta == (0, 3)
    assert switches[0].thread_id == "worker"

    replay = System2.replay_system(_FakeReader(trace))

    assert replay.gateway_pair.external(lambda: "live") == ("wrapped", "recorded")
    assert len(replay.internal_space.call_at_calls) == 1
    thread_id, cursor, on_hit, on_missed = replay.internal_space.call_at_calls[0]
    assert thread_id == 1
    assert cursor == (3,)
    assert replay.handoff.to_calls == []

    on_hit()
    assert replay.handoff.to_calls == ["worker"]
    with pytest.raises(ReplayThreadScheduleError):
        on_missed()


def test_record_system_thread_switch_hook_is_internal_space_local(monkeypatch):
    fake_retrace = _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)
    callback = system.internal_space.thread_switch
    writer.calls.clear()

    previous_delta = (1, 2)
    callback(previous_delta, "thread-2")

    assert writer.calls == [("thread_switch", previous_delta, "thread-2")]


def test_patch_function_returns_external_gateway_wrapper(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System2.record_system(writer)

    def external(value):
        return f"external:{value}"

    patched = system.patch_function(external)
    assert system.is_bound(patched)
    writer.calls.clear()

    assert patched("x") == ("wrapped", "external:('wrapped', 'x')")
    assert writer.calls[-1] == ("result", ("wrapped", "external:('wrapped', 'x')"))


def test_patch_type_returns_unpatcher(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System2.record_system(_FakeWriter())
    calls = []

    class Target:
        pass

    def patch_type(cls):
        calls.append(("patch", cls))

    def unpatch_type(cls):
        calls.append(("unpatch", cls))

    system.type_patcher.patch_type = patch_type
    system.type_patcher.unpatch_type = unpatch_type

    unpatch = system.patch_type(Target)
    unpatch()

    assert calls == [("patch", Target), ("unpatch", Target)]


def test_patch_type_record_then_replay_uses_recorded_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    messages = []
    value = ["recorded"]

    class External:
        def read(self):
            return value[0]

    def run(system):
        return system.internal_space.apply(lambda: External().read())

    record_system = System2.record_system(DefaultTraceWriter(messages.append))
    record_system.immutable_types.update({str, type(None)})
    unpatch_record = record_system.patch_type(External)
    try:
        assert run(record_system) == "recorded"
    finally:
        unpatch_record()

    value[0] = "live"
    replay_system = System2.replay_system(_FakeReader(messages))
    replay_system.immutable_types.update({str, type(None)})
    unpatch_replay = replay_system.patch_type(External)
    try:
        assert run(replay_system) == "recorded"
    finally:
        unpatch_replay()


def test_replay_system_external_call_reads_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        ResultMessage("recorded"),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    assert system.gateway_pair.external(live_target) == "recorded"


def test_replay_system_external_call_raises_recorded_error(monkeypatch):
    _install_fake_retrace(monkeypatch)
    error = ValueError("recorded")
    system = System2.replay_system(_FakeReader([
        BindOpenMessage(0),
        ErrorMessage(error),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    with pytest.raises(ValueError, match="recorded") as raised:
        system.gateway_pair.external(live_target)

    assert raised.value is error
