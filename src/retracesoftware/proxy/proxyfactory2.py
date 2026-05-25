from typing import Any, Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from retracesoftware.gateway._dynamicproxy import ProxyRef
from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory

def proxy(type_to_proxytype):
    """Create a callable that wraps a value in a proxy type."""
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, type_to_proxytype),
        functional.identity,
    )

class ProxyFactory:
    """Locked proxy creation boundary for System.

    Keep this constructor and public attributes stable.  ``ProxyFactory`` is
    the single gateway from System-level wiring into proxy type generation:
    callers provide a Binder, GatewayPair, and lifecycle callbacks, then
    consume ``typefactory``, ``proxy_internal``, ``proxy_external``, and
    ``proxy_type``.

    ``on_new_instance`` is only for retrace-owned instances produced by
    ``extend_type``.  Those instances are bound through the Binder before the
    hook runs.  Dynamic external/internal proxy creation binds through
    ``binder`` directly and must not call ``on_new_instance``.

    ``on_del`` is broader: generated owned instances and dynamic internal
    proxy instances both use ``__del__`` to report that a bound proxy object is
    going away.  Dynamic external proxy instances do not own deletion; the
    external object lifetime is represented by the dynamic external proxy type
    binding instead.
    """

    def __init__(
        self,
        *,
        binder,
        gateway_pair,
        on_new_instance: Callable[[Any], Any] = utils.noop,
        on_del: Callable[[Any], Any] = utils.noop,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
    ) -> None:
        self._dynamic_external_proxy_types = set()
        self.is_dynamic_external_proxy = utils.FastTypePredicate(
            lambda cls: cls in self._dynamic_external_proxy_types
        ).istypeof

        def bind_new_instance(value):
            if binder.lookup(value) is None:
                binder.bind(value)
            on_new_instance(value)

        def bind_external_proxy_value(value):
            if isinstance(value, type):
                self._dynamic_external_proxy_types.add(value)
            binder.autobind(value)
            return binder.lookup(value)

        def bind_retrace_type_value(value):
            binder.autobind(value)
            return binder.lookup(value)

        self.typefactory = ProxyTypeFactory(
            gateway_pair = gateway_pair,
            on_new_instance = bind_new_instance,
            on_del = on_del,
            binding_for = binder.lookup,
            bind_external_proxy_value = bind_external_proxy_value,
            bind_retrace_type_value = bind_retrace_type_value,
            proxy_type_customizer = proxy_type_customizer,
        )

        internal_proxy = proxy(self.typefactory.dynamic_internal_type)

        def proxy_internal(value):
            if binder.lookup(value) is not None:
                return value
            proxied = internal_proxy(value)
            binder.bind(proxied)
            return proxied

        self.proxy_internal = proxy_internal

        def from_spec(*args, **kwargs):
            return self.typefactory.dynamic_external_type_from_spec(*args, **kwargs)

        from_spec_callback = gateway_pair.wrap_as_callback(from_spec)
        bind_retrace_type_value(from_spec_callback)

        def dynamic_external_proxy(cls):
            proxytype = self.typefactory.dynamic_external_type(
                cls = cls,
                from_spec = from_spec_callback,
            )
            return functional.partial(utils.create_wrapped, proxytype)

        def proxy_external(value):
            if utils.is_wrapped(value) or binder.lookup(value) is not None:
                return value
            return dynamic_external_proxy(type(value))(value)

        self.proxy_external = proxy_external
        self.dynamic_external_type = self.typefactory.dynamic_external_type

    def is_dynamic_external_proxy_type(self, cls):
        return cls in self._dynamic_external_proxy_types

    def materialize_dynamic_external_proxy(self, value):
        if isinstance(value, ProxyRef):
            return value()
        if isinstance(value, type):
            if value in self._dynamic_external_proxy_types:
                return utils.create_wrapped(value, None)
            uninitialized = getattr(value, "__retrace_uninitialized__", None)
            if uninitialized is not None:
                return uninitialized()
        return value

    def proxy_type(self, cls):
        try:
            return self.typefactory.extended_type(cls)
        except TypeError:
            return self.dynamic_external_type(cls)
