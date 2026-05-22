from types import SimpleNamespace

import retracesoftware.utils as utils

from retracesoftware.proxy.proxyfactory2 import ProxyFactory


class _Binder:
    def __init__(self):
        self.bindings = {}
        self.next_handle = 0

    def bind(self, value):
        self._bind(value)

    def autobind(self, value):
        self._bind(value)

    def unbind(self, value):
        self.bindings.pop(id(value), None)

    def lookup(self, value):
        return self.bindings.get(id(value))

    def __call__(self, value):
        binding = self.lookup(value)
        if binding is None:
            return value
        return binding

    def _bind(self, value):
        if id(value) not in self.bindings:
            self.bindings[id(value)] = ("binding", self.next_handle)
            self.next_handle += 1


def _gateway_pair():
    def passthrough(target, *args, **kwargs):
        target = utils.try_unwrap(target)
        args = tuple(utils.try_unwrap(arg) for arg in args)
        kwargs = {
            name: utils.try_unwrap(value)
            for name, value in kwargs.items()
        }
        return target(*args, **kwargs)

    return SimpleNamespace(
        external=passthrough,
        internal=passthrough,
        wrap_as_callback=lambda function: function,
    )


def test_dynamic_external_proxy_predicate_identifies_proxy_instances():
    binder = _Binder()
    factory = ProxyFactory(binder=binder, gateway_pair=_gateway_pair())

    class External:
        pass

    external = External()
    proxied = factory.proxy_external(external)
    proxy_type = type(proxied)

    assert factory.is_dynamic_external_proxy(proxied)
    assert binder(proxy_type) == proxy_type.__retrace_type_binding__
    assert not factory.is_dynamic_external_proxy(external)


def test_materialize_dynamic_external_proxy_hydrates_proxy_type():
    binder = _Binder()
    factory = ProxyFactory(binder=binder, gateway_pair=_gateway_pair())

    class External:
        pass

    proxied = factory.proxy_external(External())
    proxy_type = type(proxied)
    materialized = factory.materialize_dynamic_external_proxy(proxy_type)

    assert type(materialized) is proxy_type
    assert utils.try_unwrap(materialized) is None
    assert factory.materialize_dynamic_external_proxy(External) is External


def test_dynamic_proxy_creation_does_not_call_on_new_instance():
    binder = _Binder()
    calls = []
    factory = ProxyFactory(
        binder=binder,
        gateway_pair=_gateway_pair(),
        on_new_instance=lambda value: calls.append(("on_new_instance", value)),
    )

    class External:
        pass

    class Internal:
        pass

    factory.proxy_external(External())
    factory.proxy_internal(Internal())

    assert calls == []


def test_dynamic_internal_proxy_deletion_calls_on_del():
    binder = _Binder()
    calls = []
    factory = ProxyFactory(
        binder=binder,
        gateway_pair=_gateway_pair(),
        on_del=lambda value: calls.append(("on_del", value)),
    )

    class Internal:
        pass

    proxy = factory.proxy_internal(Internal())
    proxy.__del__()
    proxy.__del__()

    assert calls == [("on_del", proxy)]
