import types
import retracesoftware.gateway._dynamicproxy as dynamicproxy
import retracesoftware.gateway._gatewaypair as gatewaypair_module
import retracesoftware.proxy.system as system_module
import retracesoftware.stream as stream
import retracesoftware.utils as utils
import pytest

from retracesoftware.gateway._dynamicproxy import ProxyRef
from retracesoftware.proxy.contracts import AsyncCapture
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    DefaultTraceWriter,
    ErrorMessage,
    GCMessage,
    OnStartMessage,
    ResultMessage,
    RunCompletedMessage,
    RunToCoordinateMessage,
    SignalMessage,
    SwitchThreadMessage,
)
from retracesoftware.install import ReplayDivergence
from retracesoftware.proxy.system import ReplayThreadScheduleError, System
from retracesoftware.proxy.typeextender import replay_shape_type


def test_thread_cursors_advance_returns_full_cursor():
    cursors = system_module._ThreadCursors()

    assert cursors.advance("main", (0, 1, 2)) == (1, 2)
    assert cursors.advance("main", (1, 7)) == (1, 7)
    assert cursors.advance("worker", (0, 3)) == (3,)
    assert cursors.advance("worker", None) is None


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
        space = getattr(gatewaypair_module._active_space, "current", None)
        if space is None:
            space = _FakeSpace.current
        key = space.id if space is not None else None
        return self.mapping.get(key, self.default)(*args, **kwargs)


class _TestBinder:
    def __init__(self, *, on_delete=utils.noop, on_unbind=None):
        self.next_handle = 0
        self.bindings = {}
        self.on_delete = on_delete
        self.on_unbind = on_unbind

    def bind(self, value):
        if id(value) not in self.bindings:
            self.bindings[id(value)] = stream.Binding((0, self.next_handle))
            self.next_handle += 1

    autobind = bind

    def unbind(self, value):
        binding = self.bindings.pop(id(value), None)
        if binding is not None:
            if self.on_unbind is not None:
                self.on_unbind(binding)
            else:
                self.on_delete(system_module._binding_handle(binding))

    def lookup(self, value):
        return self.bindings.get(id(value))

    def __call__(self, value):
        binding = self.lookup(value)
        if binding is None:
            return value
        return binding

    @staticmethod
    def add_bind_support(_target):
        return None

    set_bind_support = add_bind_support

    @staticmethod
    def remove_bind_support(_target):
        return None


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

    def signal_callback(self, fn, args, kwargs):
        self.calls.append(("signal_callback", fn, args, kwargs))

    def gc_collect(self, generation):
        self.calls.append(("gc_collect", generation))

    def error(self, error):
        self.calls.append(("error", error))

    def result(self, value):
        self.calls.append(("result", value))

    def thread_switch(self, cursor_delta, thread_id):
        self.run_to_coordinate(cursor_delta)
        self.switch_thread(thread_id)

    def run_to_coordinate(self, cursor_delta):
        self.calls.append(("run_to_coordinate", cursor_delta))

    def switch_thread(self, thread_id):
        self.calls.append(("switch_thread", thread_id))

    def checkpoint(self, cursor_delta, thread_id, value):
        self.calls.append(("checkpoint", cursor_delta, thread_id, value))

    def binding_delete(self, binding):
        self.calls.append(("binding_delete", system_module._binding_handle(binding)))


class _FakeReader:
    def __init__(self, messages):
        self.messages = list(messages)

    def __call__(self):
        return self.messages.pop(0)


class _AutoBindReader:
    def __init__(self):
        self.handle = 0

    def __call__(self):
        handle = self.handle
        self.handle += 1
        return BindOpenMessage(handle)


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


def _install_fake_retrace(monkeypatch, *, patch_proxy=True):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(system_module, "retrace", fake_retrace)
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    monkeypatch.setattr(system_module.stream, "Binder", _TestBinder)

    original_init = System.__init__

    def init_with_test_immutables(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.immutable_types.update({str, int, bool, type})
        self._refresh_type_predicates()

    monkeypatch.setattr(System, "__init__", init_with_test_immutables)
    if patch_proxy:
        monkeypatch.setattr(dynamicproxy, "proxy", _tagged_proxy("wrapped"))
    return fake_retrace


def _manual_gateway_system(monkeypatch, *, delete_binding=utils.noop):
    _install_fake_retrace(monkeypatch)
    calls = []
    system = None

    def unwrap(value):
        if isinstance(value, type):
            original = system.original_type_for(value) if system is not None else None
            if original is not None:
                return original
            return getattr(value, "__retrace_target_class__", value)
        return utils.try_unwrap(value)

    def observed(value):
        if isinstance(value, type):
            return unwrap(value)
        return value

    def external(target, *args, **kwargs):
        target = unwrap(target)
        calls.append((
            target,
            tuple(observed(arg) for arg in args),
            {name: observed(value) for name, value in kwargs.items()},
        ))
        args = tuple(unwrap(arg) for arg in args)
        kwargs = {
            name: unwrap(value)
            for name, value in kwargs.items()
        }
        return target(*args, **kwargs)

    system = System(binder=_TestBinder(on_unbind=delete_binding))
    system.gateway_pair.set_handlers(internal=external, external=external)
    return system, calls


def _replay_gateway_system(monkeypatch):
    _install_fake_retrace(monkeypatch)
    calls = []

    def external(target, *args, **kwargs):
        target = utils.try_unwrap(target)
        calls.append((target, args, kwargs))
        if getattr(target, "__name__", None) == "__new__":
            return target(*args, **kwargs)
        if getattr(target, "__name__", None) == "__init__":
            return None
        return "recorded"

    def internal(target, *args, **kwargs):
        target = utils.try_unwrap(target)
        calls.append((target, args, kwargs))
        return target(*args, **kwargs)

    system = System(binder=_TestBinder())
    system.gateway_pair.set_handlers(internal=internal, external=external)
    return system, calls


def _external_call(system, *args, **kwargs):
    return system.run_internal(system.gateway_pair.external, *args, **kwargs)


def _internal_call(system, *args, **kwargs):
    return system_module._space_apply(system.external_space, system.gateway_pair.internal, *args, **kwargs)


def test_record_system_creates_gateway_pair_and_proxy_factory(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()

    system = System.record_system(writer=writer, debug=False)

    assert system.gateway_pair is not None
    assert system.proxy_factory.typefactory.gateway_pair is system.gateway_pair


def test_system_passes_proxy_type_customizer_to_gateway_factory(monkeypatch):
    _install_fake_retrace(monkeypatch)
    customizer = object()

    system = System(
        binder=_TestBinder(),
        proxy_type_customizer=customizer,
    )

    assert system.proxy_factory.typefactory.proxy_type_customizer is customizer


def test_disable_for_uses_retrace_disable_when_available(monkeypatch):
    fake_retrace = _install_fake_retrace(monkeypatch)
    calls = []

    def disable(function):
        def wrapper(*args, **kwargs):
            calls.append("retrace.disable")
            return function(*args, **kwargs)

        return wrapper

    fake_retrace.disable = disable
    system = System.record_system(writer=_FakeWriter(), debug=False)

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
    fake_retrace = _install_fake_retrace(monkeypatch)
    calls = []

    def disable(function):
        def wrapper(*args, **kwargs):
            calls.append("retrace.disable")
            return function(*args, **kwargs)

        return wrapper

    fake_retrace.disable = disable
    system = System.record_system(writer=_FakeWriter(), debug=False)

    def helper():
        calls.append(("helper", system.enabled()))

    system.disable_for(helper, retrace=False)()

    assert calls == [("helper", False)]


def test_system_phase_dispatch_uses_retrace_space_dispatch(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.record_system(writer=_FakeWriter(), debug=False)
    dispatch = system.dispatch(
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


def test_record_system_proxy_type_customizer_sees_generated_external_type(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    customizations = []

    class External:
        def ping(self):
            return "pong"

    system = System.record_system(
        writer=writer,
        debug=False,
        proxy_type_customizer=lambda **kwargs: customizations.append(kwargs),
    )

    _external_call(system, lambda: External())

    assert len(customizations) == 1
    customization = customizations[0]
    assert customization["module"] == External.__module__
    assert customization["name"] == External.__qualname__
    assert issubclass(customization["cls"], utils.ExternalWrapped)


def test_dynamic_external_proxy_serialized_representation_is_proxy_type(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    customizations = []

    class External:
        pass

    system = System.record_system(
        writer=writer,
        debug=False,
        proxy_type_customizer=lambda **kwargs: customizations.append(kwargs),
    )

    _external_call(system, lambda: External())
    proxy_type = customizations[0]["cls"]
    proxy = ProxyRef(proxy_type)()
    binding = proxy_type.__retrace_type_binding__

    assert isinstance(proxy, utils.ExternalWrapped)
    assert isinstance(binding, stream.Binding)
    assert proxy.__retrace_serialized__() is binding


def test_dynamic_internal_proxy_serialized_representation_is_binding(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    seen = []

    class Internal:
        pass

    system = System.record_system(writer=writer, debug=False)

    result = _external_call(system, 
        lambda value: seen.append(value),
        Internal(),
    )

    assert result is None
    assert len(seen) == 1

    proxy = seen[0]
    binding = system.binder.lookup(proxy)

    assert isinstance(proxy, utils.InternalWrapped)
    assert isinstance(binding, stream.Binding)
    assert proxy.__retrace_serialized__() is binding


def test_extend_type_is_idempotent_and_records_mappings(monkeypatch):
    system, _calls = _manual_gateway_system(monkeypatch)

    class External:
        pass

    retrace_type = system.extend_type(External, python_distribution=True)

    assert system.extend_type(External, python_distribution=True) is retrace_type
    assert not hasattr(system, "type_extender")
    assert not hasattr(system.proxy_factory, "original_to_retrace_type")
    assert system.retrace_type_for(External) is retrace_type
    assert system.original_type_for(retrace_type) is External
    assert system.is_retrace_type(retrace_type)
    assert system.extended_type_flags[External] == {
        "kind": "extended",
        "python_distribution": True,
    }


def test_extend_type_stores_binding_and_unregisters_on_delete(monkeypatch):
    deleted = []
    system, _calls = _manual_gateway_system(
        monkeypatch,
        delete_binding=deleted.append,
    )

    class External:
        pass

    retrace_type = system.extend_type(External, python_distribution=False)

    assert "_retrace" + "_binding" not in retrace_type.__slots__
    assert "_retrace_deleted" in retrace_type.__slots__

    class PublicExternal(retrace_type):
        pass

    type_binding = system.binder.lookup(retrace_type)
    subtype_binding = system.binder.lookup(PublicExternal)
    obj = PublicExternal()
    binding = system.binder.lookup(obj)
    serialized = obj.__retrace_serialized__()
    obj.__del__()
    obj.__del__()

    assert type_binding is not None
    assert subtype_binding is not None
    assert subtype_binding != type_binding
    assert binding is not None
    assert serialized == binding
    assert deleted == [binding]
    assert system.binder.lookup(obj) is None
    assert system.original_type_for(PublicExternal) is External
    assert system.is_retrace_type(PublicExternal)
    assert system.is_retrace_instance(obj)


def test_record_system_typeextender_uses_record_register_path(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        pass

    retrace_type = system.extend_type(External, python_distribution=False)
    type_binding = system.binder.lookup(retrace_type)
    writer.calls.clear()

    obj = system.run_internal(retrace_type)
    binding = system.binder.lookup(obj)
    results = [call[1] for call in writer.calls if call[0] == "result"]

    assert isinstance(type_binding, stream.Binding)
    assert isinstance(binding, stream.Binding)
    assert binding != type_binding
    assert results[0] == type_binding
    obj.__del__()

    assert ("binding_delete", binding.handle) in writer.calls


def test_extend_type_methods_route_through_external_gateway(monkeypatch):
    system, calls = _manual_gateway_system(monkeypatch)

    class External:
        def read(self, value):
            return f"read:{value}"

    retrace_type = system.extend_type(External, python_distribution=False)
    obj = system.run_internal(retrace_type)
    calls.clear()

    assert system.run_internal(obj.read, "value") == "read:value"
    assert calls == [(External.read, (obj, "value"), {})]


def test_extend_type_wraps_init_and_tracks_lifecycle_in_new(monkeypatch):
    system, calls = _manual_gateway_system(monkeypatch)

    class External:
        pass

    retrace_type = system.extend_type(External, python_distribution=False)

    assert "__init__" in retrace_type.__dict__
    assert "__new__" in retrace_type.__dict__

    obj = system.run_internal(retrace_type)

    assert calls[-1] == (External.__init__, (obj,), {})


def test_extend_type_dynamic_companion_constructor_unwraps_proxy_class(monkeypatch):
    system, calls = _manual_gateway_system(monkeypatch)

    class External:
        def __new__(cls, value):
            instance = object.__new__(cls)
            instance.value = f"new:{value}"
            return instance

        def __init__(self, value):
            self.value = f"init:{value}"

    retrace_type = system.extend_type(External, python_distribution=False)
    companion_type = system.proxy_factory.typefactory.dynamic_external_type(External)
    calls.clear()

    obj = system.run_internal(companion_type, "payload")

    assert obj.__class__ is retrace_type
    assert isinstance(obj, retrace_type)
    assert isinstance(utils.try_unwrap(obj), External)
    assert utils.try_unwrap(obj).value == "init:payload"
    assert calls == [
        (External.__new__, (External, "payload"), {}),
        (External.__init__, (obj, "payload"), {}),
    ]


def test_extend_type_wraps_subclass_overrides_as_callbacks(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        def read(self):
            return "base"

    retrace_type = system.extend_type(External, python_distribution=False)

    class PublicExternal(retrace_type):
        def read(self):
            return "override"

    obj = system.run_internal(PublicExternal)
    writer.calls.clear()

    assert system_module._space_apply(system.external_space, obj.read) == "override"
    binding = system.binder.lookup(obj)
    assert (
        "callback",
        utils.try_unwrap(PublicExternal.__dict__["read"]),
        (binding,),
        {},
    ) in writer.calls


def test_extend_type_leaves_new_subclass_methods_unwrapped(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        def read(self):
            return "base"

    retrace_type = system.extend_type(External, python_distribution=False)

    class PublicExternal(retrace_type):
        def extra(self):
            return "extra"

    obj = PublicExternal()
    writer.calls.clear()

    assert obj.extra() == "extra"
    assert not utils.is_wrapped(PublicExternal.__dict__["extra"])
    assert writer.calls == []


def test_replay_shape_type_feeds_extend_type_without_original_inheritance(monkeypatch):
    system, calls = _replay_gateway_system(monkeypatch)

    class External:
        def read(self, value):
            raise AssertionError("shape type should not execute original method")

    shape_type = replay_shape_type(External)
    with pytest.raises(RuntimeError, match="cannot execute live"):
        shape_type()

    shape_obj = object.__new__(shape_type)
    with pytest.raises(RuntimeError, match="cannot execute live"):
        shape_obj.read("value")

    retrace_type = system.extend_type(shape_type, python_distribution=False)
    obj = system.run_internal(retrace_type, "constructor", ignored=True)
    calls.clear()

    assert system.extend_type(shape_type, python_distribution=False) is retrace_type
    assert system.retrace_type_for(shape_type) is retrace_type
    assert system.original_type_for(retrace_type) is shape_type
    assert system.extended_type_flags[shape_type] == {
        "kind": "extended",
        "python_distribution": False,
    }
    assert not issubclass(shape_type, External)
    assert not issubclass(retrace_type, External)
    assert issubclass(retrace_type, shape_type)
    assert shape_type.__slots__ == ()
    assert "_retrace" + "_binding" not in retrace_type.__slots__
    assert "_retrace_deleted" in retrace_type.__slots__
    assert retrace_type.__retrace_original_type__ is External

    assert system.run_internal(obj.read, "value") == "recorded"

    target, args, kwargs = calls[-1]
    assert target.__retrace_shape_method__ == (External, "read")
    assert args == (obj, "value")
    assert kwargs == {}


def test_replay_shape_type_subclass_overrides_follow_extend_type_callbacks(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        def read(self):
            return "base"

    shape_type = replay_shape_type(External)
    retrace_type = system.extend_type(shape_type, python_distribution=False)

    class PublicExternal(retrace_type):
        def read(self):
            return "override"

        def extra(self):
            self.seen = True
            return "extra"

    obj = object.__new__(PublicExternal)
    writer.calls.clear()

    assert system_module._space_apply(system.external_space, obj.read) == "override"
    subtype_binding = system.binder.lookup(PublicExternal)
    assert (
        "callback",
        utils.try_unwrap(PublicExternal.__dict__["read"]),
        (subtype_binding,),
        {},
    ) in writer.calls

    writer.calls.clear()

    assert obj.extra() == "extra"
    assert obj.seen is True
    assert not utils.is_wrapped(PublicExternal.__dict__["extra"])
    assert writer.calls == []


def test_replay_system_extend_type_shapes_non_python_distribution(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_AutoBindReader())

    class External:
        def read(self):
            return "live"

    retrace_type = system.extend_type(External, python_distribution=False)

    assert system.extend_type(External, python_distribution=False) is retrace_type
    assert system.retrace_type_for(External) is retrace_type
    assert system.original_type_for(retrace_type) is External
    assert system.extended_type_flags[External] == {
        "kind": "extended",
        "python_distribution": False,
    }
    assert not issubclass(retrace_type, External)
    assert retrace_type.__bases__[0].__retrace_shape_original_type__ is External
    assert retrace_type.__retrace_original_type__ is External


def test_replay_system_extend_type_extends_python_distribution_types(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_AutoBindReader())

    class External:
        def read(self):
            return "live"

    retrace_type = system.extend_type(External, python_distribution=True)

    assert system.extend_type(External, python_distribution=True) is retrace_type
    assert issubclass(retrace_type, External)
    assert retrace_type.__retrace_original_type__ is External
    assert system.retrace_type_for(External) is retrace_type
    assert system.original_type_for(retrace_type) is External
    assert system.extended_type_flags[External] == {
        "kind": "extended",
        "python_distribution": True,
    }


def test_replay_system_typeextender_binds_generated_instances(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: 11)
    reader = _FakeReader([])
    system = System.replay_system(reader=reader)

    class External:
        pass

    retrace_type = system.extend_type(External, python_distribution=False)
    companion_type = system.proxy_factory.typefactory.dynamic_external_type(External)
    companion_ref = system.proxy_factory.typefactory.proxy_ref(companion_type)
    companion_binding = system.binder.lookup(companion_type)
    companion_ref_binding = system.binder.lookup(companion_ref)

    assert isinstance(companion_binding, stream.Binding)
    assert isinstance(companion_ref_binding, stream.Binding)
    assert companion_binding != companion_ref_binding

    type_binding = system.binder.lookup(retrace_type)
    assert isinstance(type_binding, stream.Binding)
    assert type_binding not in (companion_binding, companion_ref_binding)

    reader.messages.extend([
        ResultMessage(type_binding),
        ResultMessage(None),
    ])

    obj = system.run_internal(retrace_type)
    binding = system.binder.lookup(obj)

    assert isinstance(binding, stream.Binding)
    assert binding not in (type_binding, companion_binding, companion_ref_binding)
    assert system.is_bound(obj)

    obj.__del__()

    assert system.binder.lookup(obj) is None


def test_wrap_type_is_idempotent_and_constructor_wraps_target(monkeypatch):
    deleted = []
    system, calls = _manual_gateway_system(monkeypatch, delete_binding=deleted.append)

    class External:
        def __new__(cls, value):
            instance = object.__new__(cls)
            instance.value = f"new:{value}"
            return instance

        def __init__(self, value):
            self.value = f"init:{value}"

    wrapper_type = system.wrap_type(External)
    wrapped = system.run_internal(wrapper_type, "payload")
    binding = system.binder.lookup(wrapper_type)

    assert system.wrap_type(External) is wrapper_type
    assert system.retrace_type_for(External) is wrapper_type
    assert system.original_type_for(wrapper_type) is External
    assert system.is_retrace_type(wrapper_type)
    assert wrapped.__class__ is wrapper_type
    assert binding is not None
    assert wrapped.__retrace_serialized__() == binding
    assert system.binder.lookup(wrapped) is None
    assert isinstance(wrapped, wrapper_type)
    assert isinstance(utils.try_unwrap(wrapped), External)
    assert utils.try_unwrap(wrapped).value == "init:payload"
    assert calls == [
        (External.__new__, (External, "payload"), {}),
        (External.__init__, (wrapped, "payload"), {}),
    ]

    calls.clear()
    system.run_internal(wrapped.__init__, "manual")

    assert utils.try_unwrap(wrapped).value == "init:manual"
    assert calls == [(External.__init__, (wrapped, "manual"), {})]

    assert deleted == []
    assert system.binder.lookup(wrapped) is None


def test_wrap_type_methods_and_properties_route_to_wrapped_target(monkeypatch):
    system, calls = _manual_gateway_system(monkeypatch)

    class External:
        def __new__(cls, value):
            instance = object.__new__(cls)
            instance._value = value
            return instance

        @property
        def value(self):
            return self._value

        def read(self, suffix):
            return f"{self._value}:{suffix}"

    wrapper_type = system.wrap_type(External)
    wrapped = system.run_internal(wrapper_type, "payload")
    calls.clear()

    assert system.run_internal(wrapped.read, "next") == "payload:next"
    assert system.run_internal(lambda: wrapped.value) == "payload"
    assert calls == [
        (External.read, (wrapped, "next"), {}),
        (getattr, (wrapped, "value"), {}),
    ]


def test_extend_type_and_wrap_type_do_not_replace_module_bindings(monkeypatch):
    system, _calls = _manual_gateway_system(monkeypatch)

    class Extensible:
        pass

    class Wrapped:
        pass

    module = types.SimpleNamespace(
        Extensible=Extensible,
        Wrapped=Wrapped,
    )

    system.extend_type(Extensible, python_distribution=True)
    system.wrap_type(Wrapped)

    assert module.Extensible is Extensible
    assert module.Wrapped is Wrapped


def test_record_system_external_call_writes_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    writer.calls.clear()

    result = _external_call(system, lambda value: f"result:{value}", "x")

    assert result == "result:x"
    assert writer.calls[-1] == ("result", "result:x")


def test_record_system_external_call_leaves_none_unproxied(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    writer.calls.clear()

    assert _external_call(system, lambda: None) is None
    assert writer.calls[-1] == ("result", None)


def test_record_system_callback_writes_callback(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    writer.calls.clear()

    def callback(value):
        return f"callback:{value}"

    assert _internal_call(system, callback, "x") == "callback:x"
    assert ("callback", callback, ("x",), {}) in writer.calls


def test_record_system_callback_observes_call_only(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    writer.calls.clear()
    seen = []

    def callback(value, *, label):
        seen.append((value, label, system.location))
        return "callback-result"

    assert _internal_call(system, callback, "arg", label="kw") == "callback-result"

    assert seen == [("arg", "kw", "internal")]
    assert writer.calls == [
        ("callback", callback, ("arg",), {"label": "kw"}),
    ]


def test_record_system_external_call_error_records_and_preserves_exception(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    writer.calls.clear()

    def target(value):
        assert value == "arg"
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom") as raised:
        _external_call(system, target, "arg")

    assert ("error", raised.value) in writer.calls


def test_record_system_bind_encodes_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class Bound:
        pass

    obj = Bound()
    system.bind(obj)
    writer.calls.clear()

    _external_call(system, lambda: obj)

    assert len(writer.calls) == 1
    event, value = writer.calls[0]
    assert event == "result"
    assert isinstance(value, stream.Binding)


def test_record_system_bind_encodes_result_containers(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class Bound:
        pass

    obj = Bound()
    system.bind(obj)
    binding = system.binder.lookup(obj)
    writer.calls.clear()

    _external_call(system, 
        lambda: [obj, {"value": obj}, (obj,)],
    )

    assert writer.calls == [
        ("result", [binding, {"value": binding}, (binding,)]),
    ]


def test_record_system_callback_encodes_nested_container_bindings(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class Bound:
        pass

    obj = Bound()
    system.bind(obj)
    binding = system.binder.lookup(obj)
    writer.calls.clear()

    def callback(items, *, label):
        return None

    _internal_call(system,
        callback,
        [obj, (obj,)],
        label={"value": obj},
    )

    assert (
        "callback",
        callback,
        ([binding, (binding,)],),
        {"label": {"value": binding}},
    ) in writer.calls


def test_record_system_passes_dynamic_external_proxy_to_writer(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class ExternalProxy(utils.ExternalWrapped):
        pass

    proxy = ProxyRef(ExternalProxy)()
    writer.calls.clear()

    _external_call(system, lambda: proxy)

    assert writer.calls == [("result", proxy)]


def test_record_system_encodes_dynamic_external_proxy_in_containers(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        pass

    proxy = system.proxy_factory.proxy_external(External())
    binding = type(proxy).__retrace_type_binding__
    writer.calls.clear()

    _external_call(system, 
        lambda: [proxy, {"value": proxy}, (proxy,)],
    )

    assert writer.calls == [
        ("result", [binding, {"value": binding}, (binding,)]),
    ]


def test_record_system_external_result_uses_extended_dynamic_companion(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        def read(self):
            return "value"

    retrace_type = system.extend_type(External, python_distribution=False)
    companion_type = system.proxy_factory.typefactory.dynamic_external_type(External)
    external = External()

    assert companion_type.__retrace_type_binding__ is not None

    writer.calls.clear()

    result = _external_call(system, lambda: external)
    proxied = system.proxy_factory.proxy_external(external)

    assert type(proxied) is companion_type
    assert proxied.__class__ is retrace_type
    assert isinstance(proxied, retrace_type)
    assert utils.try_unwrap(proxied) is external
    assert type(result) is companion_type
    assert result.__class__ is retrace_type
    assert isinstance(result, retrace_type)
    assert utils.try_unwrap(result) is external
    assert system.binder.lookup(proxied) is None
    assert system.binder.lookup(companion_type) == companion_type.__retrace_type_binding__
    assert system.binder.lookup(External) is None
    assert writer.calls == [("result", companion_type.__retrace_type_binding__)]


def test_record_system_external_result_proxy_records_later_attr_read(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        __slots__ = ("value",)

        def __init__(self):
            self.value = "recorded"

    proxy_type = system.proxy_factory.typefactory.dynamic_external_type(External)
    external = External()
    writer.calls.clear()

    seen = []

    def work():
        result = system.gateway_pair.external(lambda: external)
        seen.append(result)
        return result.value

    value = system.run_internal(work)
    result = seen[0]

    assert type(result) is proxy_type
    assert utils.try_unwrap(result) is external
    assert value == "recorded"
    assert system.binder.lookup(External) is None
    assert writer.calls == [
        ("result", proxy_type.__retrace_type_binding__),
        ("result", "recorded"),
    ]


def test_record_system_passes_dynamic_external_proxy_argument_through(monkeypatch):
    _install_fake_retrace(monkeypatch, patch_proxy=False)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class External:
        pass

    proxy = system.proxy_factory.proxy_external(External())
    target = utils.try_unwrap(proxy)
    seen = []

    _external_call(system, lambda value: seen.append(value), proxy)

    assert seen == [target]


def test_record_system_writes_bound_object_as_binding(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    class Bound:
        pass

    obj = Bound()
    system.bind(obj)
    writer.calls.clear()

    _external_call(system, lambda: obj)

    assert len(writer.calls) == 1
    event, value = writer.calls[0]
    assert event == "result"
    assert isinstance(value, stream.Binding)


def test_system_add_immutable_types_updates_passthrough_policy(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.record_system(writer=_FakeWriter(), debug=False)

    class LaterImmutable:
        pass

    class ImmutableSubclass(LaterImmutable):
        pass

    value = LaterImmutable()
    subclass_value = ImmutableSubclass()
    assert not system.is_immutable(value)
    assert not system.is_immutable(subclass_value)

    system.add_immutable_type(LaterImmutable)
    system.add_immutable_type(str)
    system.add_immutable_types(bytes, type(None))

    assert system.is_immutable(value)
    assert system.is_immutable(subclass_value)
    assert system.is_immutable("value")
    assert system.is_immutable(b"value")
    assert system.is_immutable(None)


def test_record_system_checkpoint_writes_cursor_delta_and_encoded_value(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    trace = []
    system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        debug=False,
    )
    system.internal_space.thread_delta_value = (1, 2)

    system.checkpoint({"state": "ok"})

    checkpoints = [
        message for message in trace
        if isinstance(message, CheckpointMessage)
    ]
    assert len(checkpoints) == 1
    assert checkpoints[0].cursor_delta == (1, 2)
    assert checkpoints[0].thread_id == "main"
    assert checkpoints[0].value == {"state": "ok"}


def test_record_system_run_writes_lifecycle_markers(monkeypatch):
    _install_fake_retrace(monkeypatch)
    trace = []
    system = System.record_system(writer=DefaultTraceWriter(trace.append))

    assert system.run(lambda: "done") == "done"

    assert isinstance(trace[0], OnStartMessage)
    assert isinstance(trace[-1], RunCompletedMessage)


def test_record_system_run_writes_completed_after_error(monkeypatch):
    _install_fake_retrace(monkeypatch)
    trace = []
    system = System.record_system(writer=DefaultTraceWriter(trace.append))

    with pytest.raises(ValueError):
        system.run(lambda: (_ for _ in ()).throw(ValueError("boom")))

    assert isinstance(trace[0], OnStartMessage)
    assert isinstance(trace[-1], RunCompletedMessage)


def test_record_system_debug_checkpoints_external_call_target(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    trace = []
    system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        debug=True,
    )
    system.internal_space.thread_delta_value = (1, 2)

    def target(value):
        return f"result:{value}"

    result = _external_call(system, target, "x")

    assert result == "result:x"
    checkpoint_index = next(
        index for index, message in enumerate(trace)
        if isinstance(message, CheckpointMessage)
    )
    result_index = next(
        index for index, message in enumerate(trace)
        if isinstance(message, ResultMessage)
    )
    checkpoint = trace[checkpoint_index]
    assert checkpoint_index < result_index
    assert checkpoint.cursor_delta == (1, 2)
    assert checkpoint.thread_id == "main"
    assert checkpoint.value is target


def test_debug_record_then_replay_external_call(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    trace = []

    def target(value):
        return f"result:{value}"

    record_system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        debug=True,
    )
    record_system.internal_space.thread_delta_value = (0, 1, 2)

    recorded = _external_call(record_system, target, "x")

    replay_system = System.replay_system(
        reader=_FakeReader(trace),
        debug=True,
    )
    replay_system.internal_space.coordinates_value = (1, 2)

    assert _external_call(replay_system, target, "x") == recorded


def test_record_system_capture_signals_records_handler_as_callback(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    installed = []

    def signal_signal(signum, handler):
        installed.append((signum, handler))
        return "previous-handler"

    monkeypatch.setattr(system_module._signal, "signal", signal_signal)
    trace = []
    system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        async_capture=AsyncCapture(signal=True),
    )
    system.internal_space.thread_delta_value = (0, 4, 5)
    calls = []
    frame = object()

    def handler(signum, received_frame):
        calls.append((signum, received_frame))
        return "handled"

    assert system_module._signal.signal(7, handler) == "previous-handler"
    wrapped_handler = installed[0][1]
    assert wrapped_handler is not handler
    assert system.internal_space.apply(wrapped_handler, 7, frame) == "handled"

    assert calls == [(7, frame)]

    callbacks = [
        message for message in trace
        if isinstance(message, SignalMessage)
    ]
    run_to = [
        message for message in trace
        if isinstance(message, RunToCoordinateMessage)
    ]
    assert len(run_to) == 1
    assert run_to[0].cursor_delta == (0, 4, 5)
    assert len(callbacks) == 1
    assert callbacks[0].fn is handler
    assert callbacks[0].args == (7, None)
    assert callbacks[0].kwargs == {}

    del system
    system_module.gc.collect()
    assert system_module._signal.signal is signal_signal


def test_record_system_capture_signals_does_not_record_outside_internal_space(monkeypatch):
    _install_fake_retrace(monkeypatch)
    installed = []

    def signal_signal(signum, handler):
        installed.append((signum, handler))
        return "previous-handler"

    monkeypatch.setattr(system_module._signal, "signal", signal_signal)
    trace = []
    system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        async_capture=AsyncCapture(signal=True),
    )
    calls = []
    frame = object()

    def handler(signum, received_frame):
        calls.append((signum, received_frame))
        return "handled"

    system_module._signal.signal(7, handler)
    wrapped_handler = installed[0][1]

    assert wrapped_handler(7, frame) == "handled"
    assert system.external_space.apply(wrapped_handler, 8, frame) == "handled"

    assert calls == [(7, frame), (8, frame)]
    assert not any(
        isinstance(message, (RunToCoordinateMessage, SignalMessage))
        for message in trace
    )

    del system
    system_module.gc.collect()
    assert system_module._signal.signal is signal_signal


def test_replay_system_schedules_signal_callback_at_cursor(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    calls = []

    def handler(signum, frame):
        calls.append((signum, frame))

    system = System.replay_system(reader=_FakeReader([
        RunToCoordinateMessage((0, 4, 5)),
        SignalMessage(handler, (7, None), {}),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"
    assert calls == []
    assert len(system.internal_space.call_at_calls) == 1

    cursor, on_hit, on_missed = system.internal_space.call_at_calls[0]
    assert cursor == (4, 5)

    on_hit()
    assert calls == [(7, None)]
    with pytest.raises(ReplayThreadScheduleError):
        on_missed()


def test_record_system_capture_gc_records_collection_at_coordinate(monkeypatch):
    _install_fake_retrace(monkeypatch)
    callbacks = []
    monkeypatch.setattr(system_module.gc, "callbacks", callbacks)
    trace = []
    system = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        async_capture=AsyncCapture(gc=True),
    )
    system.internal_space.thread_delta_value = (0, 8, 13)

    assert len(callbacks) == 1
    callbacks[0]("start", {"generation": 1})
    callbacks[0]("stop", {"generation": 1})

    run_to = [
        message for message in trace
        if isinstance(message, RunToCoordinateMessage)
    ]
    gc_messages = [
        message for message in trace
        if isinstance(message, GCMessage)
    ]
    assert len(run_to) == 1
    assert run_to[0].cursor_delta == (0, 8, 13)
    assert len(gc_messages) == 1
    assert gc_messages[0].generation == 1

    del system
    system_module.gc.collect()
    assert callbacks == []


def test_replay_system_schedules_gc_at_coordinate(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    collects = []
    monkeypatch.setattr(system_module.gc, "collect", lambda generation: collects.append(generation))
    system = System.replay_system(reader=_FakeReader([
        RunToCoordinateMessage((0, 8, 13)),
        GCMessage(1),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"
    assert collects == []
    assert len(system.internal_space.call_at_calls) == 1

    cursor, on_hit, on_missed = system.internal_space.call_at_calls[0]
    assert cursor == (8, 13)

    on_hit()
    assert collects == [1]
    with pytest.raises(ReplayThreadScheduleError):
        on_missed()


def test_replay_system_resolves_bound_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    obj = object()
    system = System.replay_system(reader=_FakeReader([
        ResultMessage(stream.Binding(0)),
    ]))
    system.bind(obj)

    def live_target():
        raise AssertionError("live target should not run")

    assert _external_call(system, live_target) is obj


def test_replay_system_hydrates_dynamic_external_proxy_type_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    reader = _FakeReader([])
    system = System.replay_system(reader=reader)

    class External:
        pass

    proxied = system.proxy_factory.proxy_external(External())
    proxy_type = type(proxied)
    binding = system.binder.lookup(proxy_type)
    reader.messages.append(ResultMessage(binding))

    result = _external_call(system, lambda: "live")

    assert type(result) is proxy_type
    assert utils.try_unwrap(result) is None


def test_replay_system_checkpoint_accepts_matching_value(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), {"state": "ok"}, thread_id="main"),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    system.checkpoint({"state": "ok"})


def test_replay_system_checkpoint_raises_on_value_difference(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), {"state": "record"}, thread_id="main"),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    with pytest.raises(ReplayDivergence, match="checkpoint difference"):
        system.checkpoint({"state": "replay"})


def test_replay_system_checkpoint_raises_on_cursor_difference(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), {"state": "ok"}, thread_id="main"),
    ]))
    system.internal_space.coordinates_value = (1, 3)

    with pytest.raises(ReplayDivergence, match="checkpoint cursor difference"):
        system.checkpoint({"state": "ok"})


def test_replay_system_checkpoint_raises_on_thread_difference(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "replay-thread")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), {"state": "ok"}, thread_id="record-thread"),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    with pytest.raises(ReplayDivergence, match="checkpoint thread difference"):
        system.checkpoint({"state": "ok"})


def test_replay_system_checkpoint_tolerates_unresolved_binding_payload(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), stream.Binding((0, 99)), thread_id="main"),
    ]))
    system.internal_space.coordinates_value = (1, 2)

    system.checkpoint(object())


def test_replay_system_materializes_unresolved_dynamic_external_constructor_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: "main")
    system = System.replay_system(reader=_FakeReader([
        CheckpointMessage((0, 1, 2), stream.Binding((0, 10)), thread_id="main"),
        ResultMessage(stream.Binding((0, 11))),
    ]), debug=True)
    system.internal_space.coordinates_value = (1, 2)

    class External:
        pass

    proxy_type = system.proxy_factory.dynamic_external_type(External)
    obj = system.run_internal(proxy_type)

    assert type(obj) is proxy_type
    assert utils.try_unwrap(obj) is None


def test_replay_system_consumes_binding_delete_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    obj = object()
    system = System.replay_system(reader=_FakeReader([
        BindCloseMessage(0),
        ResultMessage("ok"),
    ]))
    system.bind(obj)

    assert _external_call(system, lambda: "live") == "ok"


def test_replay_system_consumes_multiple_binding_deletes_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    first = object()
    second = object()
    system = System.replay_system(reader=_FakeReader([
        BindCloseMessage(0),
        BindCloseMessage(1),
        ResultMessage("done"),
    ]))
    system.bind(first)
    system.bind(second)

    assert _external_call(system, lambda: "live") == "done"


def test_replay_system_runs_callback_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    calls = []

    def callback(value):
        calls.append((_FakeSpace.current, value))

    system = System.replay_system(reader=_FakeReader([
        CallbackMessage(callback, ("x",), {}),
        ResultMessage(None),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"
    assert calls == [(system.internal_space, "x")]


def test_replay_system_drops_callback_completion_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    calls = []

    def callback(value):
        calls.append((_FakeSpace.current, value))

    system = System.replay_system(reader=_FakeReader([
        CallbackMessage(callback, ("x",), {}),
        CallbackResultMessage("callback-result"),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"
    assert calls == [(system.internal_space, "x")]


def test_replay_system_binds_callback_completion_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    callback_result = object()

    def callback():
        return callback_result

    system = System.replay_system(reader=_FakeReader([
        CallbackMessage(callback, (), {}),
        ResultMessage(stream.Binding(0)),
        ResultMessage(stream.Binding(0)),
    ]))

    assert _external_call(system, lambda: "live") is callback_result


def test_replay_system_run_internal_external_gateway_reads_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        ResultMessage("recorded"),
    ]))
    seen = []

    def live_target(value, *, label):
        seen.append((value, label))
        raise AssertionError("live target should not run")

    result = system.run_internal(
        system.ext_gateway,
        live_target,
        "arg",
        label="kw",
    )

    assert result == "recorded"
    assert seen == []


def test_replay_system_root_space_control_plane_does_not_consume_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        ResultMessage("recorded"),
    ]))
    observed = []

    def root_control_plane():
        observed.append(system.location)
        return "live"

    assert system.root_space.wrap(root_control_plane)() == "live"
    assert system.run_internal(system.gateway_pair.external, lambda: "should-not-run") == "recorded"
    assert observed == [None]


def test_monitoring_root_adapter_patched_call_does_not_consume_result(monkeypatch):
    from retracesoftware.install.monitoring_system import root_disable_for

    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        ResultMessage("recorded"),
    ]))
    root_external = root_disable_for(system)(
        system.gateway_pair.external,
        unwrap_args=False,
    )

    assert root_external(lambda: "live") == "live"
    assert _external_call(system, lambda: "should-not-run") == "recorded"


def test_replay_system_resolves_callback_bindings(monkeypatch):
    _install_fake_retrace(monkeypatch)
    calls = []
    obj = object()

    def callback(value):
        calls.append(value)

    system = System.replay_system(reader=_FakeReader([
        CallbackMessage(callback, (stream.Binding(0),), {}),
        ResultMessage(None),
        ResultMessage("ok"),
    ]))
    system.bind(obj)

    assert _external_call(system, lambda: "live") == "ok"
    assert calls == [obj]


def test_replay_system_schedules_thread_switch_before_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: 1)
    system = System.replay_system(reader=_FakeReader([
        RunToCoordinateMessage((0, 3, 5)),
        SwitchThreadMessage("worker"),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"

    assert len(system.internal_space.call_at_calls) == 1
    cursor, on_hit, on_missed = system.internal_space.call_at_calls[0]
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
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: 1)
    system = System.replay_system(reader=_FakeReader([
        RunToCoordinateMessage((0, 1, 2)),
        SwitchThreadMessage("worker"),
        RunToCoordinateMessage((1, 7)),
        SwitchThreadMessage("main"),
        ResultMessage("ok"),
    ]))

    assert _external_call(system, lambda: "live") == "ok"

    first, second = system.internal_space.call_at_calls
    assert first[0] == (1, 2)
    assert second[0] == (1, 7)
    assert system.handoff.to_calls == []
    first[1]()
    second[1]()
    assert system.handoff.to_calls == ["worker", "main"]


def test_recorded_thread_switch_replays_as_scheduled_handoff(monkeypatch):
    _install_fake_retrace(monkeypatch)
    monkeypatch.setattr(system_module._thread, "get_ident", lambda: 1)
    trace = []
    record = System.record_system(
        writer=DefaultTraceWriter(trace.append),
        debug=False,
    )

    record.internal_space.thread_switch((0, 3), "worker")
    assert _external_call(record, lambda: "recorded") == "recorded"

    run_to_messages = [
        message for message in trace
        if isinstance(message, RunToCoordinateMessage)
    ]
    switches = [message for message in trace if isinstance(message, SwitchThreadMessage)]
    assert len(run_to_messages) == 1
    assert run_to_messages[0].cursor_delta == (0, 3)
    assert len(switches) == 1
    assert switches[0].thread_id == "worker"

    replay = System.replay_system(reader=_FakeReader(trace))

    assert _external_call(replay, lambda: "live") == "recorded"
    assert len(replay.internal_space.call_at_calls) == 1
    cursor, on_hit, on_missed = replay.internal_space.call_at_calls[0]
    assert cursor == (3,)
    assert replay.handoff.to_calls == []

    on_hit()
    assert replay.handoff.to_calls == ["worker"]
    with pytest.raises(ReplayThreadScheduleError):
        on_missed()


def test_record_system_thread_switch_hook_is_internal_space_local(monkeypatch):
    fake_retrace = _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)
    callback = system.internal_space.thread_switch
    writer.calls.clear()

    previous_delta = (1, 2)
    callback(previous_delta, "thread-2")

    assert writer.calls == [
        ("run_to_coordinate", previous_delta),
        ("switch_thread", "thread-2"),
    ]


def test_record_system_async_capture_can_disable_thread_switch(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(
        writer=writer,
        debug=False,
        async_capture=AsyncCapture(thread_switch=False),
    )
    callback = system.internal_space.thread_switch
    writer.calls.clear()

    callback((1, 2), "thread-2")

    assert writer.calls == []


def test_patch_function_returns_external_gateway_wrapper(monkeypatch):
    _install_fake_retrace(monkeypatch)
    writer = _FakeWriter()
    system = System.record_system(writer=writer, debug=False)

    def external(value):
        return f"external:{value}"

    patched = system.patch_function(external)
    assert system.is_bound(patched)
    writer.calls.clear()

    assert system.run_internal(patched, "x") == "external:x"
    assert writer.calls[-1] == ("result", "external:x")


def test_patch_function_for_type_preregisters_external_type_materialization(monkeypatch):
    _install_fake_retrace(monkeypatch)
    reader = _FakeReader([])
    system = System.replay_system(reader=reader)

    class External:
        pass

    system.patch_function(External)
    external_type_binding = system.binder.lookup(External)
    proxy_type = system.retrace_type_for(External)

    assert external_type_binding is not None
    assert proxy_type is not None

    reader.messages.append(ResultMessage(external_type_binding))

    result = _external_call(system, lambda: "live")

    assert type(result) is proxy_type
    assert utils.try_unwrap(result) is None


def test_proxy_type_generates_retrace_type(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.record_system(writer=_FakeWriter(), debug=False)

    class Target:
        pass

    retrace_type = system.proxy_type(Target)

    assert issubclass(retrace_type, Target)
    assert retrace_type is not Target


def test_proxy_type_record_then_replay_uses_recorded_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    messages = []
    value = ["recorded"]

    class External:
        def read(self):
            return value[0]

    record_system = System.record_system(
        writer=DefaultTraceWriter(messages.append),
        debug=False,
    )
    record_system.immutable_types.update({str, type(None)})
    RecordExternal = record_system.proxy_type(External)
    assert record_system.run(lambda: RecordExternal().read()) == "recorded"

    value[0] = "live"
    replay_system = System.replay_system(reader=_FakeReader(messages))
    replay_system.immutable_types.update({str, type(None)})
    ReplayExternal = replay_system.proxy_type(External)
    assert replay_system.run(lambda: ReplayExternal().read()) == "recorded"


def test_replay_system_external_call_reads_result(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        ResultMessage("recorded"),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    assert _external_call(system, live_target) == "recorded"


def test_replay_system_skips_run_completed_marker(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        OnStartMessage(),
        RunCompletedMessage(),
        ResultMessage("recorded"),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    assert _external_call(system, live_target) == "recorded"


def test_replay_system_continues_after_run_to_completed_marker(monkeypatch):
    _install_fake_retrace(monkeypatch)
    system = System.replay_system(reader=_FakeReader([
        RunToCoordinateMessage(None),
        RunCompletedMessage(),
        ResultMessage("recorded"),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    assert _external_call(system, live_target) == "recorded"
    assert len(system.internal_space.call_at_calls) == 0


def test_replay_system_consumes_lifecycle_markers_around_run(monkeypatch):
    _install_fake_retrace(monkeypatch)
    reader = _FakeReader([
        OnStartMessage(),
        RunCompletedMessage(),
    ])
    system = System.replay_system(reader=reader)

    assert system.run(lambda: "done") == "done"
    assert reader.messages == []


def test_replay_system_consumes_binding_delete_before_run_completed_marker(monkeypatch):
    _install_fake_retrace(monkeypatch)
    reader = _FakeReader([
        OnStartMessage(),
        BindCloseMessage(0),
        RunCompletedMessage(),
    ])
    system = System.replay_system(reader=reader)

    assert system.run(lambda: "done") == "done"
    assert reader.messages == []


def test_replay_system_external_call_raises_recorded_error(monkeypatch):
    _install_fake_retrace(monkeypatch)
    error = ValueError("recorded")
    system = System.replay_system(reader=_FakeReader([
        ErrorMessage(error),
    ]))

    def live_target():
        raise AssertionError("live target should not run")

    with pytest.raises(ValueError, match="recorded") as raised:
        _external_call(system, live_target)

    assert raised.value is error
