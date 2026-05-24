from types import SimpleNamespace

import pytest

import retracesoftware.utils as utils

from retracesoftware.gateway._dynamicproxy import ProxyRef
from retracesoftware.gateway._proxytype import DynamicProxy, Proxy
from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory


def _factory():
    calls = []
    bindings = {}
    next_handle = [0]

    def on_new_instance(instance):
        if id(instance) not in bindings:
            bindings[id(instance)] = ("owned", next_handle[0], instance)
            next_handle[0] += 1
        calls.append(("on_new_instance", instance, bindings[id(instance)]))

    def on_del(instance):
        calls.append(("on_del", instance, bindings.get(id(instance))))

    def binding_for(instance):
        return bindings.get(id(instance))

    def unwrap(value):
        if isinstance(value, type):
            return getattr(value, "__retrace_target_class__", value)
        return utils.try_unwrap(value)

    def passthrough(target, *args, **kwargs):
        target = unwrap(target)
        args = tuple(unwrap(arg) for arg in args)
        kwargs = {
            name: unwrap(value)
            for name, value in kwargs.items()
        }
        return target(*args, **kwargs)

    gateway_pair = SimpleNamespace(
        external=passthrough,
        internal=passthrough,
    )
    return ProxyTypeFactory(
        gateway_pair=gateway_pair,
        on_new_instance=on_new_instance,
        on_del=on_del,
        binding_for=binding_for,
    ), calls


def test_proxy_type_factory_creates_dynamic_external_type():
    factory, calls = _factory()

    class External:
        pass

    proxy_type = factory.dynamic_external_type(External)
    proxy_ref = ProxyRef(proxy_type)()

    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert issubclass(proxy_type, DynamicProxy)
    assert proxy_ref.__retrace_serialized__() is proxy_type.__retrace_type_binding__
    assert proxy_type.__retrace_type_binding__ is None
    assert calls == []


def test_proxy_type_factory_dynamic_external_type_accepts_from_spec():
    factory, _calls = _factory()
    received = {}

    class External:
        def read(self):
            return "value"

    def from_spec(**kwargs):
        received.update(kwargs)
        return factory.dynamic_external_type_from_spec(**kwargs)

    proxy_type = factory.dynamic_external_type(External, from_spec=from_spec)

    assert received["module"] == External.__module__
    assert received["name"] == External.__qualname__
    assert "read" in received["methods"]
    assert "read" in received["attrs"]
    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert ProxyRef(proxy_type)().__retrace_serialized__() is (
        proxy_type.__retrace_type_binding__
    )


def test_proxy_type_factory_binds_dynamic_external_method_wrappers():
    bound = []
    gateway_pair = SimpleNamespace(
        external=lambda target, *args, **kwargs: target(*args, **kwargs),
        internal=lambda target, *args, **kwargs: target(*args, **kwargs),
    )
    factory = ProxyTypeFactory(
        gateway_pair=gateway_pair,
        bind_external_proxy_value=lambda value: bound.append(value),
    )

    class External:
        def read(self):
            return "value"

    factory.dynamic_external_type(External)

    targets = [
        utils.try_unwrap(value)
        for value in bound
        if utils.is_wrapped(value)
    ]
    assert External.read in targets


def test_proxy_type_factory_binds_extended_method_wrappers():
    bound = []
    gateway_pair = SimpleNamespace(
        external=lambda target, *args, **kwargs: target(*args, **kwargs),
        internal=lambda target, *args, **kwargs: target(*args, **kwargs),
    )
    factory = ProxyTypeFactory(
        gateway_pair=gateway_pair,
        bind_retrace_type_value=lambda value: bound.append(value),
    )

    class External:
        def read(self):
            return "value"

    factory.extended_type(External)

    targets = [
        utils.try_unwrap(value)
        for value in bound
        if utils.is_wrapped(value)
    ]
    assert External.read in targets


def test_proxy_type_factory_dynamic_external_type_reuses_registered_wrapper_type():
    factory, _calls = _factory()
    received = {}

    class External:
        def read(self):
            return "value"

    retrace_type = factory.dynamic_external_type(External)

    def from_spec(**kwargs):
        received.update(kwargs)
        return factory.dynamic_external_type_from_spec(**kwargs)

    proxy_type = factory.dynamic_external_type(External, from_spec=from_spec)
    proxy = utils.create_wrapped(proxy_type, External())

    assert proxy_type is retrace_type
    assert proxy.__class__ is retrace_type
    assert isinstance(proxy, retrace_type)
    assert received == {}


def test_proxy_type_factory_dynamic_external_proxy_reuses_registered_wrapper_type():
    factory, _calls = _factory()

    class External:
        pass

    retrace_type = factory.dynamic_external_type(External)
    constructor = factory.dynamic_external_proxy(External)
    proxy = constructor(External())

    assert proxy.__class__ is retrace_type
    assert isinstance(proxy, retrace_type)
    assert type(proxy) is retrace_type


def test_proxy_type_factory_extended_type_registers_dynamic_external_companion():
    factory, _calls = _factory()
    received = {}

    class External:
        def read(self):
            return "value"

    retrace_type = factory.extended_type(External)

    def from_spec(**kwargs):
        received.update(kwargs)
        return factory.dynamic_external_type_from_spec(**kwargs)

    companion_type = factory.dynamic_external_type(External)
    proxy_type = factory.dynamic_external_type(External, from_spec=from_spec)
    proxy = utils.create_wrapped(proxy_type, External())

    assert companion_type is proxy_type
    assert proxy_type is not retrace_type
    assert proxy.__class__ is retrace_type
    assert isinstance(proxy, retrace_type)
    assert received == {}
    assert ProxyRef(proxy_type)().__retrace_serialized__() is (
        proxy_type.__retrace_type_binding__
    )


def test_proxy_type_factory_creates_dynamic_external_type_from_spec():
    factory, _calls = _factory()

    proxy_type = factory.dynamic_external_type_from_spec(
        module=__name__,
        name="SpecExternal",
        methods=["read"],
        attrs=["read", "value"],
    )

    assert proxy_type.__module__ == __name__
    assert proxy_type.__name__ == "SpecExternal"
    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert issubclass(proxy_type, DynamicProxy)
    assert "read" in proxy_type.__dict__
    assert ProxyRef(proxy_type)().__retrace_serialized__() is (
        proxy_type.__retrace_type_binding__
    )


def test_proxy_type_factory_creates_dynamic_internal_type():
    factory, calls = _factory()

    class Internal:
        pass

    proxy_type = factory.dynamic_internal_type(Internal)

    assert issubclass(proxy_type, utils.InternalWrapped)
    assert issubclass(proxy_type, DynamicProxy)
    assert calls == []


def test_proxy_type_factory_rejects_proxy_types_as_source_classes():
    factory, _calls = _factory()

    class External:
        pass

    dynamic_external = factory.dynamic_external_type(External)
    dynamic_internal = factory.dynamic_internal_type(External)
    extended = factory.extended_type(External)

    for proxy_type in (dynamic_external, dynamic_internal, extended):
        assert issubclass(proxy_type, Proxy)
        with pytest.raises(AssertionError):
            factory.dynamic_external_type(proxy_type)
        with pytest.raises(AssertionError):
            factory.dynamic_internal_type(proxy_type)
        with pytest.raises(AssertionError):
            factory.extended_type(proxy_type)


def test_proxy_type_factory_dynamic_internal_type_calls_on_del():
    factory, calls = _factory()

    class Internal:
        pass

    proxy_type = factory.dynamic_internal_type(Internal)
    proxy = utils.create_wrapped(proxy_type, Internal())

    proxy.__del__()
    proxy.__del__()

    assert ("on_del", proxy, None) in calls
    assert not hasattr(proxy, "__retrace" + "_binding__")
    assert len([call for call in calls if call[0] == "on_del" and call[1] is proxy]) == 1


def test_proxy_type_factory_creates_extended_type():
    factory, calls = _factory()

    class External:
        pass

    proxy_type = factory.extended_type(External)
    obj = proxy_type()
    binding = obj.__retrace_serialized__()

    assert issubclass(proxy_type, External)
    assert obj.__retrace_serialized__() == binding
    assert ("on_new_instance", obj, binding) in calls

    obj.__del__()

    assert binding[0] == "owned"
    assert ("on_del", obj, binding) in calls


def test_proxy_type_factory_extended_subclass_init_calls_on_new_instance():
    factory, calls = _factory()

    class External:
        def __init__(self):
            self.base_seen = True

    proxy_type = factory.extended_type(External)

    class PublicExternal(proxy_type):
        def __init__(self, value):
            self.value = value

    obj = PublicExternal("payload")
    binding = obj.__retrace_serialized__()

    assert obj.value == "payload"
    assert binding[0] == "owned"
    assert ("on_new_instance", obj, binding) in calls


def test_proxy_type_factory_creates_wrapping_dynamic_external_type():
    factory, calls = _factory()
    events = []

    class External:
        def __new__(cls, value):
            instance = object.__new__(cls)
            instance.value = f"new:{value}"
            events.append(("new", value))
            return instance

        def __init__(self, value):
            self.value = f"init:{value}"
            events.append(("init", value))

    proxy_type = factory.dynamic_external_type(External)
    obj = proxy_type("payload")

    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert isinstance(utils.try_unwrap(obj), External)
    assert utils.try_unwrap(obj).value == "init:payload"
    assert obj.__retrace_serialized__() is None
    assert calls == []
    assert events == [("new", "payload"), ("init", "payload")]

    obj.__init__("manual")

    assert utils.try_unwrap(obj).value == "init:manual"
    assert events == [
        ("new", "payload"),
        ("init", "payload"),
        ("init", "manual"),
    ]


def test_proxy_type_factory_wrapping_dynamic_external_type_calls_constructor_init():
    factory, _calls = _factory()
    init_calls = []

    class External:
        def __init__(self, value):
            init_calls.append(value)

    proxy_type = factory.dynamic_external_type(External)
    obj = proxy_type("payload")

    assert isinstance(utils.try_unwrap(obj), External)
    assert init_calls == ["payload"]


def test_proxy_type_factory_wrapping_dynamic_external_type_preserves_new_args():
    factory, _calls = _factory()

    class External:
        pass

    proxy_type = factory.dynamic_external_type(External)

    with pytest.raises(TypeError, match="External\\(\\) takes no arguments"):
        proxy_type("payload")
