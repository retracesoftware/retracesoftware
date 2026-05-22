from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.gateway._dynamicproxy import (
    ProxyRef,
    ProxytypeFactory,
    _ext_proxytype_from_spec_with,
    _has_custom_getattr,
    _has_instance_dict,
)
from retracesoftware.gateway._proxytype import method_names, superdict
from retracesoftware.proxy.typeextender import TypeExtender


class ProxyTypeFactory:
    """Generate the proxy type shapes used by System.

    This class is intentionally a coordinator, not a registry. ``System``
    owns original-to-retrace mappings, replay policy flags, binding tables,
    and lifecycle policy. The factory only builds proxy type shapes and wires
    lifecycle callbacks into generated owned proxy instances.
    """

    def __init__(
        self,
        *,
        gateway_pair,
        on_new_instance: Callable[[Any], Any] = utils.noop,
        on_del: Callable[[Any], Any] = utils.noop,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
    ) -> None:
        self.gateway_pair = gateway_pair
        self.on_new_instance = on_new_instance
        self.on_del = on_del
        self.proxy_type_customizer = proxy_type_customizer

        self.type_extender = TypeExtender(
            on_new_instance=on_new_instance,
            on_del=on_del,
            gateway_pair=gateway_pair,
        )
        self.dynamic_proxy_factory = ProxytypeFactory(
            internal=SimpleNamespace(gateway=gateway_pair.internal),
            external=SimpleNamespace(gateway=gateway_pair.external),
            bind=utils.noop,
            is_patched_type=utils.FastTypePredicate(lambda cls: False).istypeof,
            proxy_ref=functional.memoize_one_arg(ProxyRef),
            customize_proxy_type=proxy_type_customizer,
            on_del=on_del,
        )

    def dynamic_external_type(self, cls: type, *, from_spec=None) -> type:
        if from_spec is None:
            from_spec = self.dynamic_external_type_from_spec

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        methods = [method for method in method_names(cls) if method not in blacklist]
        attrs = [name for name in superdict(cls) if name not in blacklist]

        return from_spec(
            module=cls.__module__,
            name=cls.__qualname__,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=_has_custom_getattr(cls),
            has_instance_dict=_has_instance_dict(cls),
        )

    def dynamic_external_type_from_spec(
        self,
        *,
        module: str,
        name: str,
        methods,
        attrs,
        has_custom_getattr: bool = False,
        has_instance_dict: bool = False,
    ) -> type:
        return _ext_proxytype_from_spec_with(
            wrap_ext=lambda target: self._wrapped_external_method(target),
            bind=utils.noop,
            proxy_ref=functional.memoize_one_arg(ProxyRef),
            customize_proxy_type=self.proxy_type_customizer,
            module=module,
            name=name,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=has_custom_getattr,
            has_instance_dict=has_instance_dict,
        )

    def dynamic_internal_type(self, cls: type) -> type:
        return self.dynamic_proxy_factory.int_proxytype(cls)

    def extended_type(self, cls: type) -> type:
        return self.type_extender.extend_type(cls)

    def instantiable_external_type(self, cls: type) -> type:
        return self.type_extender.wrap_type(cls)

    def _wrapped_external_method(self, target):
        wrapped = utils.wrapped_function(
            handler=self.gateway_pair.external,
            target=target,
        )
        return wrapped
