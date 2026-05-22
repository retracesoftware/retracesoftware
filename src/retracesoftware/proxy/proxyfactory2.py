from typing import Any, Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory

def proxy(type_to_proxytype):
    """Create a callable that wraps a value in a proxy type."""
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, type_to_proxytype),
        functional.identity,
    )

class ProxyFactory:
    """Locked proxy creation boundary for System2.

    Keep this constructor and public attributes stable.  ``ProxyFactory`` is
    the single gateway from System2-level wiring into proxy type generation:
    callers provide a Binder and GatewayPair, then consume ``typefactory``,
    ``proxy_internal``, ``proxy_external``, and ``proxy_type``.  If surrounding
    record/replay wiring changes, adapt that wiring to this interface rather
    than growing new proxy creation backdoors.
    """

    def __init__(
        self,
        *,
        binder,
        gateway_pair,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
    ) -> None:
        self.typefactory = ProxyTypeFactory(
            gateway_pair = gateway_pair,
            on_new_instance = binder.bind,
            on_del = binder.unbind,
            proxy_type_customizer = proxy_type_customizer,
        )

        internal_proxy = proxy(self.typefactory.dynamic_internal_type)

        def proxy_internal(value):
            proxied = internal_proxy(value)
            binder.bind(proxied)
            return proxied

        self.proxy_internal = proxy_internal

        def from_spec(*args, **kwargs):
            proxytype = self.typefactory.dynamic_external_type_from_spec(*args, **kwargs)
            binder.autobind(proxytype)
            proxytype.__retrace_type_binding__ = binder.lookup(proxytype)
            return proxytype

        from_spec_callback = gateway_pair.wrap_as_callback(from_spec)

        def dynamic_external_type(cls):
            return self.typefactory.dynamic_external_type(cls = cls, from_spec = from_spec_callback)

        self.proxy_external = proxy(dynamic_external_type)

    def proxy_type(self, cls):
        try:
            return self.typefactory.extended_type(cls)
        except TypeError:
            return self.typefactory.instantiable_external_type(cls)
