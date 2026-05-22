from types import SimpleNamespace

import retracesoftware.utils as utils

from retracesoftware.gateway._dynamicproxy import ProxyRef
from retracesoftware.gateway._proxytype import DynamicProxy
from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory


def _factory():
    calls = []

    def on_new_instance(instance):
        binding = ("owned", len(calls), instance)
        object.__setattr__(instance, "_retrace_binding", binding)
        calls.append(("on_new_instance", instance, instance.__retrace_binding__()))

    def on_del(instance):
        calls.append(("on_del", instance, instance.__retrace_binding__()))

    def passthrough(target, *args, **kwargs):
        target = utils.try_unwrap(target)
        args = tuple(utils.try_unwrap(arg) for arg in args)
        kwargs = {
            name: utils.try_unwrap(value)
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
    ), calls


def test_proxy_type_factory_creates_dynamic_external_type():
    factory, calls = _factory()

    class External:
        pass

    proxy_type = factory.dynamic_external_type(External)

    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert issubclass(proxy_type, DynamicProxy)
    assert ProxyRef(proxy_type)().__retrace_serialized__() is (
        proxy_type.__retrace_type_binding__
    )
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


def test_proxy_type_factory_dynamic_internal_type_calls_on_del():
    factory, calls = _factory()

    class Internal:
        pass

    proxy_type = factory.dynamic_internal_type(Internal)
    proxy = utils.create_wrapped(proxy_type, Internal())
    object.__setattr__(proxy, "_retrace_binding", ("dynamic-instance",))

    proxy.__del__()
    proxy.__del__()

    assert ("on_del", proxy, ("dynamic-instance",)) in calls
    assert proxy.__retrace_binding__() is None
    assert len([call for call in calls if call[0] == "on_del" and call[1] is proxy]) == 1


def test_proxy_type_factory_creates_extended_type():
    factory, calls = _factory()

    class External:
        pass

    proxy_type = factory.extended_type(External)
    obj = proxy_type()
    binding = obj.__retrace_binding__()

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
    binding = obj.__retrace_binding__()

    assert obj.value == "payload"
    assert binding[0] == "owned"
    assert ("on_new_instance", obj, binding) in calls


def test_proxy_type_factory_creates_instantiable_external_type():
    factory, calls = _factory()

    class External:
        def __init__(self, value):
            self.value = value

    proxy_type = factory.instantiable_external_type(External)
    obj = proxy_type("payload")
    binding = obj.__retrace_binding__()

    assert issubclass(proxy_type, utils.ExternalWrapped)
    assert isinstance(utils.try_unwrap(obj), External)
    assert utils.try_unwrap(obj).value == "payload"
    assert obj.__retrace_serialized__() == binding
    assert ("on_new_instance", obj, binding) in calls

    obj.__del__()

    assert binding[0] == "owned"
    assert ("on_del", obj, binding) in calls
