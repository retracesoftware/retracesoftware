import sys
import types

import pytest

import retracesoftware.utils as utils


class _FakeSpace:
    current = None
    _next_id = 1

    def __init__(self):
        self.id = _FakeSpace._next_id
        _FakeSpace._next_id += 1
        self.apply_calls = 0

    @property
    def apply(self):
        def apply(function, *args, **kwargs):
            self.apply_calls += 1
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


def _fake_retrace():
    _FakeSpace.current = None
    _FakeSpace._next_id = 1

    return types.SimpleNamespace(
        CoordinateSpace=_FakeSpace,
        root_space=_FakeSpace(),
        space_dispatch=lambda default, cases=(): _FakeSpaceDispatch(default, cases),
    )


sys.modules["retrace"] = _fake_retrace()

from retracesoftware.gateway import GatewayPair
import retracesoftware.gateway._dynamicproxy as dynamicproxy
import retracesoftware.gateway._gatewaypair as gatewaypair_module
import retracesoftware.gateway._recording as recording
from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory


class FreshExternalResult:
    value = 0

    def __init__(self):
        self.value = 0

    def ping(self):
        return "pong"


class CallbackResult:
    def ping(self):
        return "callback-pong"


def _contains(value, expected):
    if value == expected:
        return True
    if isinstance(value, tuple):
        return any(_contains(item, expected) for item in value)
    if isinstance(value, list):
        return any(_contains(item, expected) for item in value)
    if isinstance(value, dict):
        return any(
            _contains(key, expected) or _contains(item, expected)
            for key, item in value.items()
        )
    return False


def _tagged_proxy(label):
    def proxy(_proxytype_from):
        def wrap(value):
            return (label, value)

        return wrap

    return proxy


def _is_passthrough(value):
    return isinstance(value, (str, int, type(None)))


def test_recording_external_call_runs_live_target_and_observes_result(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    results = []
    errors = []
    callbacks = []
    bound = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
        bind=bound.append,
    )
    seen = []

    def external_target(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "external-result"

    result = pair.external(external_target, "arg", label="kw")

    assert result == ("wrapped", "external-result")
    assert seen[0][:2] == (("wrapped", "arg"), ("wrapped", "kw"))
    assert seen[0][2] is not None
    assert results == [("wrapped", "external-result")]
    assert errors == []
    assert callbacks == []
    assert bound == []


def test_unwired_pair_runs_passthrough_before_mode_wiring(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)

    pair = GatewayPair.create_unwired()

    assert pair.external(lambda value: f"external:{value}", "x") == "external:x"
    assert pair.internal(lambda value: f"internal:{value}", "y") == "internal:y"


def test_parameterless_gateway_pair_creates_unwired_passthrough_pair(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)

    pair = GatewayPair()

    assert pair.sandbox_space is pair._internal_endpoint.space
    assert pair._external_space is pair._external_endpoint.space
    assert pair.external(lambda value: f"external:{value}", "x") == "external:x"
    assert pair.internal(lambda value: f"internal:{value}", "y") == "internal:y"


def test_wrap_as_callback_routes_through_internal_gateway_and_binds_wrapper(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    bound = []
    calls = []
    pair = GatewayPair.create_unwired(bind=bound.append)

    def internal_handler(function, *args, **kwargs):
        calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    pair.set_handlers(
        internal=internal_handler,
        external=lambda function, *args, **kwargs: function(*args, **kwargs),
    )

    def callback(value, *, suffix):
        return f"{value}:{suffix}"

    wrapped = pair.wrap_as_callback(callback)

    assert wrapped("seen", suffix="inside") == "seen:inside"
    assert bound == [wrapped]
    assert calls == [(callback, ("seen",), {"suffix": "inside"})]


def test_unwired_pair_can_be_wired_for_recording_with_proxy_type_factory(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    callbacks = []
    results = []
    bound = []
    pair = GatewayPair.create_unwired()
    factory = ProxyTypeFactory(
        gateway_pair=pair,
        on_new_instance=bound.append,
    )

    pair.wire_recording(
        factory,
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: None,
        on_result=results.append,
    )

    assert pair.external(lambda value: value, "x") == ("wrapped", ("wrapped", "x"))
    assert results == [("wrapped", ("wrapped", "x"))]
    assert callbacks == []


def test_unwired_pair_can_be_wired_for_replay_with_proxy_type_factory(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    bound = []
    pair = GatewayPair.create_unwired()
    factory = ProxyTypeFactory(
        gateway_pair=pair,
        on_new_instance=bound.append,
    )

    pair.wire_replay(
        factory,
        is_passthrough=lambda value: False,
        next_result=lambda *args, **kwargs: "recorded",
    )

    assert pair.external(lambda: "live") == "recorded"
    with pytest.raises(RuntimeError, match="replay cannot create external proxies"):
        pair._external_endpoint.proxy(object())


def test_recording_passthrough_predicate_decides_whether_result_is_proxied(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    predicate_calls = []
    results = []

    def is_passthrough(value):
        predicate_calls.append(value)
        return value == "plain-result"

    pair = GatewayPair.create_recording_pair(
        is_passthrough=is_passthrough,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: None,
        on_result=results.append,
        bind=lambda value: None,
    )

    assert pair.external(lambda: "plain-result") == "plain-result"
    assert pair.external(lambda: "proxy-result") == ("wrapped", "proxy-result")
    assert predicate_calls == ["plain-result", "proxy-result"]
    assert results == ["plain-result", ("wrapped", "proxy-result")]


def test_recording_external_call_error_observer_is_side_effect_only(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    errors = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: errors.append(args),
        on_result=lambda result: None,
        bind=lambda value: None,
    )

    def external_target(_arg):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom") as raised:
        pair.external(external_target, "arg")

    assert len(errors) == 1
    assert errors[0][0] is ValueError
    assert errors[0][1] is raised.value


def test_recording_callback_observes_callback_but_not_result_or_error(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    results = []
    errors = []
    callbacks = []
    seen = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
        bind=lambda value: None,
    )

    def callback(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "callback-result"

    result = pair.internal(callback, "arg", label="kw")

    assert result == ("wrapped", "callback-result")
    assert seen[0][:2] == (("wrapped", "arg"), ("wrapped", "kw"))
    assert seen[0][2] is not None
    assert callbacks == [((callback, ("wrapped", "arg")), {"label": ("wrapped", "kw")})]
    assert results == []
    assert errors == []


def test_recording_external_result_uses_real_external_proxy(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    bound = []
    observed = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=_is_passthrough,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: None,
        on_result=observed.append,
        bind=bound.append,
    )

    result = pair.external(lambda: FreshExternalResult())

    assert isinstance(result, FreshExternalResult)
    assert isinstance(observed[0], utils.ExternalWrapped)
    assert pair.sandbox_space.apply(observed[0].ping) == "pong"
    assert bound == []


def test_recording_external_proxy_forwards_declared_attrs(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    observed = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=_is_passthrough,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: None,
        on_result=observed.append,
        bind=lambda value: None,
    )

    pair.external(lambda: FreshExternalResult())
    result = observed[0]

    assert pair.sandbox_space.apply(lambda: result.value) == 0
    pair.sandbox_space.apply(setattr, result, "value", 42)
    assert pair.sandbox_space.apply(lambda: result.value) == 42


def test_recording_external_call_inputs_use_real_internal_proxy(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    seen = []

    pair = GatewayPair.create_recording_pair(
        is_passthrough=_is_passthrough,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: None,
        on_result=lambda value: None,
        bind=lambda value: None,
    )

    target = FreshExternalResult()

    def external_target(value):
        seen.append(value)
        return "done"

    assert pair.external(external_target, target) == "done"
    assert isinstance(seen[0], utils.InternalWrapped)
    assert utils.unwrap(seen[0]) is target


def test_recording_callback_result_uses_real_internal_proxy(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    callback_result = CallbackResult()

    pair = GatewayPair.create_recording_pair(
        is_passthrough=_is_passthrough,
        on_callback=lambda *args, **kwargs: None,
        on_error=lambda *args: None,
        on_result=lambda value: None,
        bind=lambda value: None,
    )

    result = pair.internal(lambda: callback_result)

    assert isinstance(result, utils.InternalWrapped)
    assert utils.unwrap(result) is callback_result


def test_proxy_ref_creates_empty_external_wrapper():
    class DemoExternalWrapped(utils.ExternalWrapped):
        pass

    proxy = dynamicproxy.ProxyRef(DemoExternalWrapped)()

    assert isinstance(proxy, DemoExternalWrapped)
    assert utils.unwrap(proxy) is None


def test_replay_external_call_uses_next_result_and_does_not_run_live_target(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    seen = []

    def next_result(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "recorded-result"

    pair = GatewayPair.create_replay_pair(
        is_passthrough=lambda value: False,
        next_result=next_result,
        bind=lambda value: None,
    )

    def live_target(*args, **kwargs):
        raise AssertionError("replay must not call live external target")

    result = pair.external(live_target, "arg", label="kw")

    assert result == "recorded-result"
    assert seen[0][:2] == (("wrapped", "arg"), ("wrapped", "kw"))
    assert seen[0][2] is not None


def test_replay_callback_runs_internal_callback_and_proxies_result(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    seen = []

    pair = GatewayPair.create_replay_pair(
        is_passthrough=lambda value: False,
        next_result=lambda *args, **kwargs: "unused",
        bind=lambda value: None,
    )

    def callback(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "callback-result"

    result = pair.internal(callback, "arg", label="kw")

    assert result == ("wrapped", "callback-result")
    assert seen[0][:2] == ("arg", "kw")
    assert seen[0][2] is not None


def test_sandbox_space_is_coordinate_space_used_by_external_entry(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))

    pair = GatewayPair.create_replay_pair(
        is_passthrough=lambda value: False,
        next_result=lambda *args, **kwargs: "recorded-result",
        bind=lambda value: None,
    )

    def live_target(*args, **kwargs):
        raise AssertionError("replay must not call live external target")

    assert pair.sandbox_space.apply_calls == 0
    assert pair.external(live_target) == "recorded-result"
    assert pair.sandbox_space.apply_calls == 1


def test_recording_pair_recorder_emits_named_events(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    events = []
    bindings = {}

    pair = recording.create_recording_pair_recorder(
        record=events.append,
        is_passthrough=lambda value: False,
        bindings=bindings,
    )

    def external_target(value):
        return ("result", value)

    external_result = pair.external(external_target, "arg")
    assert any(
        isinstance(event, recording.Result)
        and recording._resolve(event.value, bindings) == external_result
        for event in events
    )
    assert not any(isinstance(event, recording.Bind) for event in events)

    def callback(value):
        return ("callback", value)

    callback_result = pair.internal(callback, "arg")
    assert callback_result is not None
    assert any(
        isinstance(event, recording.Callback)
        and event.function is callback
        and recording._resolve(event.args, bindings) == (("wrapped", "arg"),)
        and recording._resolve(event.kwargs, bindings) == {}
        for event in events
    )

    def failing_target():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        pair.external(failing_target)

    assert any(
        isinstance(event, recording.Error)
        and event.exc_type is RuntimeError
        and str(event.exc_value) == "boom"
        for event in events
    )


def test_recording_pair_recorder_records_bind_events_and_binding_dict(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    events = []
    bindings = {}
    token = object()

    pair = recording.create_recording_pair_recorder(
        record=events.append,
        is_passthrough=lambda value: False,
        bindings=bindings,
    )

    pair.external(lambda value: value, token)

    assert not any(isinstance(event, recording.Bind) for event in events)
    assert any(
        isinstance(event, recording.Result)
        and _contains(event.value, token)
        for event in events
    )


def test_replay_pair_recorder_consumes_bind_events_and_resolves_bound_values(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    token = object()
    events = [
        recording.Bind(7, token),
        recording.Result(recording.Bound(7)),
    ]
    bindings = {}

    pair = recording.create_replay_pair_recorder(
        events=events,
        is_passthrough=lambda value: False,
        bindings=bindings,
    )

    assert pair.external(lambda: "live-result") is token
    assert bindings == {7: token}
    assert events == []


def test_replay_pair_recorder_consumes_recorded_result_list(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    events = []

    record_pair = recording.create_recording_pair_recorder(
        record=events.append,
        is_passthrough=lambda value: False,
    )

    recorded = record_pair.external(lambda value: ("result", value), "arg")
    replay_events = list(events)

    replay_pair = recording.create_replay_pair_recorder(
        events=replay_events,
        is_passthrough=lambda value: False,
    )

    def live_target(*args, **kwargs):
        raise AssertionError("replay must not call live external target")

    assert replay_pair.external(live_target, "arg") == recorded
    assert replay_events == []


def test_replay_pair_recorder_consumes_recorded_error_list(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    events = []

    record_pair = recording.create_recording_pair_recorder(
        record=events.append,
        is_passthrough=lambda value: False,
    )

    def failing_target():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        record_pair.external(failing_target)

    replay_pair = recording.create_replay_pair_recorder(
        events=events,
        is_passthrough=lambda value: False,
    )

    with pytest.raises(RuntimeError, match="boom"):
        replay_pair.external(lambda: "live-result")

    assert events == []


def test_replay_pair_recorder_consumes_recorded_callback_list(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    events = []

    record_pair = recording.create_recording_pair_recorder(
        record=events.append,
        is_passthrough=lambda value: False,
    )

    def callback(value):
        return ("callback", value)

    recorded = record_pair.internal(callback, "arg")
    replay_events = list(events)

    replay_pair = recording.create_replay_pair_recorder(
        events=replay_events,
        is_passthrough=lambda value: False,
    )

    assert replay_pair.internal(callback, ("wrapped", "arg")) == recorded
    assert replay_events == []
