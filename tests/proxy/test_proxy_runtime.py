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

    def __getitem__(self, space):
        return self.mapping[self._key(space)]

    def __setitem__(self, space, function):
        self.mapping[self._key(space)] = function

    def __call__(self, *args, **kwargs):
        space = _FakeSpace.current
        function = self.mapping.get(space.id if space is not None else None, self.default)
        return function(*args, **kwargs)


def _fake_retrace(disable=None):
    _FakeSpace.current = None
    _FakeSpace._next_id = 1
    root_space = _FakeSpace()
    disabled_space = _FakeSpace()

    def space_dispatch(default, cases=()):
        return _FakeSpaceDispatch(default, cases)

    namespace = types.SimpleNamespace(
        CoordinateSpace=_FakeSpace,
        root_space=root_space,
        disabled_space=disabled_space,
        space_dispatch=space_dispatch,
    )
    if disable is not None:
        namespace.disable = disable
    return namespace


def _boundary_pair(fake_retrace, proxy_factory=None):
    if proxy_factory is None:
        proxy_factory = ProxyFactory()
    return Gateways.create(
        proxy_factory=proxy_factory,
        space_dispatch=fake_retrace.space_dispatch,
        internal_space=fake_retrace.CoordinateSpace(),
        external_space=fake_retrace.CoordinateSpace(),
    )


sys.modules["retrace"] = _fake_retrace()

from retracesoftware.proxy.io import recorder
from retracesoftware.proxy.patchtype import patch_type
import retracesoftware.gateway._dynamicproxy as dynamicproxy
import retracesoftware.gateway._gatewaypair as gatewaypair_module
import retracesoftware.proxy.system as proxy_system
from retracesoftware.gateway import GatewayPair
from retracesoftware.proxy.system import BaseSystem, Endpoint, Gateways, ObservableSystem, ProxyFactory, RecordSystem, ReplaySystem, System, create_record_boundary_pair, create_replay_boundary_pair
from retracesoftware.proxy.taggedtraceio import tagged_trace_writer
from retracesoftware.testing.memorytape import IOMemoryTape


def _run_with_replay(ext_runner):
    def replay_fn(fn, *args, **kwargs):
        return ext_runner()

    return replay_fn


def test_patch_type_is_idempotent_for_subtypes_patched_through_base():
    bound = []
    system = RecordSystem(on_bind=bound.append)

    class Base:
        def ping(self):
            return "base"

    class Child(Base):
        def ping(self):
            return "child"

    try:
        patch_type(system, Base)
        assert Base in system.patched_types
        assert Child in system.patched_types

        bound_after_base_patch = list(bound)

        assert patch_type(system, Child) is Child
        assert system.patch(Child) is Child
        assert bound == bound_after_base_patch
    finally:
        system.unpatch_types()


def test_run_with_replay_returns_ext_runner_value():
    trace_result = object()
    called = []

    def fn(*args, **kwargs):
        called.append((args, kwargs))
        return object()

    replay = _run_with_replay(lambda: trace_result)

    assert replay(fn, 1, 2, name="value") is trace_result
    assert called == []


def test_run_with_replay_propagates_recorded_error_without_calling_live_function():
    class RecordedFailure(RuntimeError):
        pass

    called = False

    def fn():
        nonlocal called
        called = True

    replay = _run_with_replay(lambda: (_ for _ in ()).throw(RecordedFailure("boom")))

    try:
        replay(fn)
    except RecordedFailure:
        pass
    else:
        assert False, "expected recorded failure to be raised"

    assert called is False


def test_disable_for_uses_retrace_disable_when_available(monkeypatch):
    calls = []

    def disable(function):
        def wrapper(*args, **kwargs):
            calls.append("retrace.disable")
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(proxy_system, "retrace", _fake_retrace(disable))

    system = RecordSystem()

    def helper(value):
        calls.append(("helper", value, system.enabled()))
        return "result"

    wrapped = system.disable_for(helper, unwrap_args=False)

    assert wrapped("value") == "result"
    assert calls == [
        "retrace.disable",
        ("helper", "value", False),
    ]


def test_disable_for_can_skip_retrace_disable(monkeypatch):
    calls = []

    def disable(function):
        def wrapper(*args, **kwargs):
            calls.append("retrace.disable")
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(proxy_system, "retrace", _fake_retrace(disable))

    system = RecordSystem()

    def helper():
        calls.append(("helper", system.enabled()))

    system.disable_for(helper, retrace=False)()

    assert calls == [("helper", False)]


def test_system_phase_dispatch_uses_retrace_space_dispatch(monkeypatch):
    calls = []

    class FakeSpace:
        current = None

        def __init__(self):
            self.name = f"space-{len(calls)}"
            self.apply_calls = 0

        @property
        def apply(self):
            def apply(function, *args, **kwargs):
                self.apply_calls += 1
                return self.run(function, *args, **kwargs)

            return apply

        def run(self, function, *args, **kwargs):
            previous = FakeSpace.current
            FakeSpace.current = self
            try:
                return function(*args, **kwargs)
            finally:
                FakeSpace.current = previous

    disabled_space = FakeSpace()

    def space_dispatch(default, cases):
        calls.append(("space_dispatch", len(cases)))
        mapping = dict(cases)

        def dispatch(*args, **kwargs):
            return mapping.get(FakeSpace.current, default)(*args, **kwargs)

        return dispatch

    fake_retrace = types.SimpleNamespace(
        CoordinateSpace=FakeSpace,
        disabled_space=disabled_space,
        space_dispatch=space_dispatch,
    )
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)

    system = RecordSystem()
    dispatch = system.create_dispatch(
        disabled=lambda: "disabled",
        external=lambda: "external",
        internal=lambda: "internal",
    )

    assert dispatch() == "disabled"
    assert system.enabled() is False
    assert system.run_internal(dispatch) == "internal"
    assert system.apply_with("external", dispatch)() == "external"

    assert system.apply_with(None, dispatch)() == "disabled"
    assert system.internal_space.apply_calls == 1
    assert system.external_space.apply_calls == 1
    assert disabled_space.apply_calls == 1
    assert calls


def test_base_system_dispatch_reports_spaces(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace)
    system = BaseSystem(boundary_pair=boundary_pair)

    dispatch = system.create_dispatch(
        disabled=lambda: "disabled",
        external=lambda: "external",
        internal=lambda: "internal",
    )

    assert dispatch() == "disabled"
    assert system.enabled() is False
    assert system.run_internal(dispatch) == "internal"
    assert system.enabled() is False
    assert system.apply_with("external", dispatch)() == "external"
    assert system.apply_with(None, dispatch)() == "disabled"
    assert system.internal_space.apply_calls == 1
    assert system.external_space.apply_calls == 1
    assert system.disabled_space.apply_calls == 1


def test_base_system_proxy_helpers_create_wrapped_values(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace)
    system = BaseSystem(boundary_pair=boundary_pair)

    class Target:
        pass

    class InternalProxy(utils.InternalWrapped):
        pass

    class ExternalProxy(utils.ExternalWrapped):
        pass

    system.int_proxytype = lambda cls: InternalProxy
    system.ext_proxytype = lambda cls: ExternalProxy

    internal_target = Target()
    internal = system.proxy_int(internal_target)

    assert isinstance(internal, InternalProxy)
    assert utils.unwrap(internal) is internal_target
    assert system.is_bound(internal)

    external_target = Target()
    external = system.proxy_ext(external_target)

    assert isinstance(external, ExternalProxy)
    assert utils.unwrap(external) is external_target


def test_base_system_accepts_prebuilt_boundary_pair(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace)
    internal = Endpoint(
        space=boundary_pair.internal.space,
        gateway=boundary_pair.internal.gateway,
        proxy=lambda value: ("internal", value),
    )
    external = Endpoint(
        space=boundary_pair.external.space,
        gateway=boundary_pair.external.gateway,
        proxy=lambda value: ("external", value),
    )
    boundary_pair = Gateways(internal=internal, external=external)

    system = BaseSystem(boundary_pair=boundary_pair)

    assert system.boundary_pair is boundary_pair
    assert system.internal is internal
    assert system.external is external
    assert system.internal_space is internal.space
    assert system.external_space is external.space
    assert system.int_gateway is internal.gateway
    assert system.ext_gateway is external.gateway
    assert system.proxy_int("value") == ("internal", "value")
    assert system.proxy_ext("value") == ("external", "value")


_TRANSFORMING_PROXY_FACTORY = ProxyFactory(
    internal=lambda value: ("int", value),
    external=lambda value: ("ext", value),
)


def test_gateway_pair_recording_wires_endpoints(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", lambda proxytype_from: lambda value: ("proxy", value))
    results = []
    errors = []
    callbacks = []
    gateway_pair = GatewayPair.create_recording_pair(
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
        bind=lambda value: None,
    )
    seen = []

    def target(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "result"

    result = gateway_pair.external(
        target,
        "arg",
        label="kw",
    )

    assert result == ("proxy", "result")
    assert seen[0][:2] == (("proxy", "arg"), ("proxy", "kw"))
    assert seen[0][2] is not None
    assert results == [("proxy", "result")]
    assert errors == []
    assert callbacks == []


def test_gateway_pair_replay_wires_endpoints(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", lambda proxytype_from: lambda value: ("proxy", value))
    seen = []

    def next_result(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "recorded"

    gateway_pair = GatewayPair.create_replay_pair(
        is_passthrough=lambda value: False,
        next_result=next_result,
        bind=lambda value: None,
    )

    def target(*args, **kwargs):
        raise AssertionError("live target should not run")

    result = gateway_pair.external(
        target,
        "arg",
        label="kw",
    )

    assert result == "recorded"
    assert seen[0][:2] == (("proxy", "arg"), ("proxy", "kw"))
    assert seen[0][2] is not None


def test_observable_external_call_preserves_target_and_observes_result(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace, _TRANSFORMING_PROXY_FACTORY)
    results = []
    errors = []
    callbacks = []
    system = ObservableSystem(
        boundary_pair=boundary_pair,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
    )
    seen = []

    def target(arg, *, label):
        seen.append((arg, label, system.location))
        return "result"

    result = system.run_internal(system.ext_gateway, target, "arg", label="kw")

    assert result == ("ext", "result")
    assert seen == [(( "int", "arg"), ("int", "kw"), "external")]
    assert results == [("ext", "result")]
    assert errors == []
    assert callbacks == []


def test_create_record_boundary_pair_returns_wired_boundary_pair(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", lambda proxytype_from: lambda value: ("proxy", value))
    results = []
    errors = []
    callbacks = []
    endpoints = _boundary_pair(fake_retrace)
    boundary_pair = create_record_boundary_pair(
        internal=endpoints.internal,
        external=endpoints.external,
        is_passthrough=lambda value: False,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
        bind=lambda value: None,
    )
    seen = []

    def target(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "result"

    result = boundary_pair.internal.space.apply(
        boundary_pair.external.gateway,
        target,
        "arg",
        label="kw",
    )

    assert result == ("proxy", "result")
    assert seen == [(( "proxy", "arg"), ("proxy", "kw"), boundary_pair.external.space)]
    assert results == [("proxy", "result")]
    assert errors == []
    assert callbacks == []


def test_observable_external_call_error_side_effect_preserves_exception(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace, _TRANSFORMING_PROXY_FACTORY)
    errors = []
    system = ObservableSystem(
        boundary_pair=boundary_pair,
        on_error=lambda *args: errors.append(args),
        on_result=lambda result: None,
        on_callback=lambda *args, **kwargs: None,
    )

    def target(value):
        assert value == ("int", "arg")
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom") as raised:
        system.run_internal(system.ext_gateway, target, "arg")

    assert len(errors) == 1
    assert errors[0][0] is ValueError
    assert errors[0][1] is raised.value
    assert errors[0][2] is not None


def test_observable_callback_observes_call_only(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace, _TRANSFORMING_PROXY_FACTORY)
    callbacks = []
    results = []
    errors = []
    system = ObservableSystem(
        boundary_pair=boundary_pair,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
    )
    seen = []

    def callback(arg, *, label):
        seen.append((arg, label, system.location))
        return "callback-result"

    result = system.apply_with("external", system.int_gateway)(callback, "arg", label="kw")

    assert result == ("int", "callback-result")
    assert seen == [(("ext", "arg"), ("ext", "kw"), "internal")]
    assert callbacks == [((callback, ("ext", "arg")), {"label": ("ext", "kw")})]
    assert results == []
    assert errors == []


def test_replay_external_call_reads_next_result_without_calling_target(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    boundary_pair = _boundary_pair(fake_retrace, _TRANSFORMING_PROXY_FACTORY)
    seen = []

    def next_result(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "recorded"

    system = ReplaySystem(
        next_result,
        boundary_pair=boundary_pair,
    )

    def target(*args, **kwargs):
        raise AssertionError("live target should not run")

    result = system.run_internal(system.ext_gateway, target, "arg", label="kw")

    assert result == "recorded"
    assert seen == [(("int", "arg"), ("int", "kw"), system.external_space)]


def test_create_replay_boundary_pair_reads_next_result(monkeypatch):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(proxy_system, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "proxy", lambda proxytype_from: lambda value: ("proxy", value))
    seen = []

    def next_result(arg, *, label):
        seen.append((arg, label, _FakeSpace.current))
        return "recorded"

    endpoints = _boundary_pair(fake_retrace)
    boundary_pair = create_replay_boundary_pair(
        internal=endpoints.internal,
        external=endpoints.external,
        is_passthrough=lambda value: False,
        next_result=next_result,
        bind=lambda value: None,
    )

    def target(*args, **kwargs):
        raise AssertionError("live target should not run")

    result = boundary_pair.internal.space.apply(
        boundary_pair.external.gateway,
        target,
        "arg",
        label="kw",
    )

    assert result == "recorded"
    assert seen == [(("proxy", "arg"), ("proxy", "kw"), boundary_pair.external.space)]

    with pytest.raises(RuntimeError, match="replay cannot create external proxies"):
        boundary_pair.external.proxy("live")


def test_recorder_async_new_patched_rejects_unpatched_types():
    tape = IOMemoryTape()
    system = recorder(
        writer=tagged_trace_writer(tape.writer().write),
        debug=False,
        stacktraces=False,
    )
    try:
        with pytest.raises(AssertionError, match="async_new_patched expected a patched type"):
            system.async_new_patched(object())
    finally:
        system.unpatch_types()
