from __future__ import annotations

from typing import Any, Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.gateway._dynamicproxy import (
    ProxyRef,
    _ext_proxytype_from_spec_with,
    _has_custom_getattr,
    _has_instance_dict,
)
from retracesoftware.gateway._proxytype import (
    DynamicProxy,
    Proxy,
    method_names,
    superdict,
)
from retracesoftware.proxy.contracts import ProxyConstructor
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
        binding_for: Callable[[Any], Any] = utils.noop,
        bind_external_proxy_value: Callable[[Any], Any] = utils.noop,
        bind_retrace_type_value: Callable[[Any], Any] = utils.noop,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
    ) -> None:
        self.gateway_pair = gateway_pair
        self.on_new_instance = on_new_instance
        self.on_del = on_del
        self.binding_for = binding_for
        self.bind_external_proxy_value = bind_external_proxy_value
        self.bind_retrace_type_value = bind_retrace_type_value
        self.proxy_type_customizer = proxy_type_customizer
        self._extended_types: dict[type, type] = {}
        self._dynamic_external_types: dict[type, type] = {}
        self.proxy_ref = functional.memoize_one_arg(ProxyRef)

        self.type_extender = TypeExtender(
            on_new_instance=on_new_instance,
            on_new_type=bind_retrace_type_value,
            bind_value=bind_retrace_type_value,
            on_del=on_del,
            binding_for=binding_for,
            gateway_pair=gateway_pair,
        )

    def _assert_unproxied_source_type(self, cls: type) -> None:
        assert isinstance(cls, type)
        assert not issubclass(cls, Proxy), (
            "ProxyTypeFactory source cls must not already be a proxy type"
        )

    def dynamic_external_type(self, cls: type, *, from_spec=None) -> type:
        self._assert_unproxied_source_type(cls)
        retrace_type = self._dynamic_external_types.get(cls)
        if retrace_type is not None:
            return retrace_type

        source_cls = self._extended_types.get(cls)
        retrace_type = self._create_dynamic_external_type(
            cls,
            source_cls=source_cls or cls,
            from_spec=from_spec,
        )
        self._dynamic_external_types[cls] = retrace_type
        return retrace_type

    def _create_dynamic_external_type(
        self,
        cls: type,
        *,
        source_cls: type,
        from_spec=None,
    ) -> type:
        source_class = cls if from_spec is None else None
        if from_spec is None:
            from_spec = self.dynamic_external_type_from_spec

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__']
        methods = [method for method in method_names(source_cls) if method not in blacklist]
        attrs = [name for name in superdict(source_cls) if name not in blacklist]
        reported_class = source_cls if source_cls is not cls else None

        spec = {
            "module": cls.__module__,
            "name": cls.__qualname__,
            "methods": methods,
            "attrs": attrs,
            "has_custom_getattr": _has_custom_getattr(source_cls),
            "has_instance_dict": _has_instance_dict(source_cls),
            "reported_class": reported_class,
        }
        if source_class is not None:
            spec["source_class"] = source_class

        return from_spec(**spec)

    def dynamic_external_proxy(self, cls: type, *, from_spec=None) -> ProxyConstructor:
        proxy_type = self.dynamic_external_type(cls, from_spec=from_spec)
        return functional.partial(utils.create_wrapped, proxy_type)

    def dynamic_external_type_from_spec(
        self,
        *,
        module: str,
        name: str,
        methods,
        attrs,
        has_custom_getattr: bool = False,
        has_instance_dict: bool = False,
        reported_class: type | None = None,
        source_class: type | None = None,
    ) -> type:
        return _ext_proxytype_from_spec_with(
            wrap_ext=lambda target: self._wrapped_external_method(target),
            bind=self.bind_external_proxy_value,
            proxy_ref=self.proxy_ref,
            customize_proxy_type=self.proxy_type_customizer,
            module=module,
            name=name,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=has_custom_getattr,
            has_instance_dict=has_instance_dict,
            reported_class=reported_class,
            source_class=source_class,
        )

    def dynamic_internal_type(self, cls: type) -> type:
        self._assert_unproxied_source_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        spec = {}

        for name in superdict(cls).keys():
            if name not in blacklist:
                try:
                    value = getattr(cls, name)
                except AttributeError:
                    continue

                if utils.is_method_descriptor(value):
                    spec[name] = self._wrapped_internal_method(value)

        spec['__getattr__'] = self._wrapped_internal_method(getattr)
        spec['__setattr__'] = self._wrapped_internal_method(setattr)

        if utils.yields_callable_instances(cls):
            spec['__call__'] = self.gateway_pair.internal

        on_del = self.on_del
        binding_for = self.binding_for

        def __del__(instance):
            try:
                deleted = object.__getattribute__(instance, "_retrace_deleted")
            except AttributeError:
                deleted = False

            if deleted:
                return

            try:
                object.__setattr__(instance, "_retrace_deleted", True)
            except Exception:
                pass

            on_del(instance)

        def __retrace_serialized__(instance):
            return binding_for(instance)

        spec['__slots__'] = ('_retrace_deleted',)
        spec['__class__'] = property(functional.constantly(cls))
        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__
        spec['__del__'] = __del__
        spec['__retrace_serialized__'] = __retrace_serialized__

        proxy_type = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)
        self.proxy_type_customizer(
            module=cls.__module__,
            name=cls.__qualname__,
            cls=proxy_type,
        )
        return proxy_type

    def extended_type(self, cls: type) -> type:
        self._assert_unproxied_source_type(cls)
        retrace_type = self._extended_types.get(cls)
        if retrace_type is None:
            retrace_type = self.type_extender.extend_type(cls)
            self._extended_types[cls] = retrace_type
            self._dynamic_external_types[cls] = self._create_dynamic_external_type(
                cls,
                source_cls=retrace_type,
            )
        return retrace_type

    def _wrapped_external_method(self, target):
        wrapped = utils.wrapped_function(
            handler=self.gateway_pair.external,
            target=target,
        )
        self.bind_external_proxy_value(wrapped)
        return wrapped

    def _wrapped_internal_method(self, target):
        wrapped = utils.wrapped_function(
            handler=self.gateway_pair.internal,
            target=target,
        )
        self.bind_retrace_type_value(wrapped)
        return wrapped
