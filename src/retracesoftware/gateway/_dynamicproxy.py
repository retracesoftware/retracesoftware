import sys

import retrace
import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.install.edgecases import patchtype
from retracesoftware.gateway._proxytype import DynamicProxy, method_names, superdict


def proxy(type_to_proxytype):
    """Create a callable that wraps a value in a proxy type."""
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, type_to_proxytype),
        functional.identity,
    )


class ProxyRef:
    def __init__(self, cls):
        self.cls = cls

    def __call__(self):
        return utils.create_wrapped(self.cls, None)


def is_proxy_ref(value):
    return isinstance(value, ProxyRef)


def lookup(module, name):
    if module in sys.modules:
        if name in sys.modules[module].__dict__:
            return sys.modules[module].__dict__[name]
    return None


_MISSING_ATTR = object()


def _lookup_type_attr(cls, name):
    if not isinstance(cls, type):
        return _MISSING_ATTR

    for base in cls.__mro__:
        if name in base.__dict__:
            return base.__dict__[name]

    return _MISSING_ATTR


def _has_custom_getattr(cls):
    return _lookup_type_attr(cls, "__getattr__") is not _MISSING_ATTR


def _has_instance_dict(cls):
    return _lookup_type_attr(cls, "__dict__") is not _MISSING_ATTR


def _raise_missing_generated_attr(cls, name):
    type_name = cls.__name__ if isinstance(cls, type) else "object"
    raise AttributeError(f"'{type_name}' object has no attribute '{name}'")


def _generated_proxy_getattr(wrap_ext, cls, attrs, has_custom_getattr):
    wrapped_getattr = wrap_ext(getattr)
    attrs = frozenset(attrs)

    def __getattr__(instance, name):
        if (
            name not in attrs
            and not has_custom_getattr
        ):
            _raise_missing_generated_attr(cls, name)

        return wrapped_getattr(instance, name)

    return __getattr__


def _generated_proxy_setattr(wrap_ext, cls, attrs, has_instance_dict):
    wrapped_setattr = wrap_ext(setattr)
    attrs = frozenset(attrs)

    def __setattr__(instance, name, value):
        if (
            name not in attrs
            and not has_instance_dict
        ):
            _raise_missing_generated_attr(cls, name)

        return wrapped_setattr(instance, name, value)

    return __setattr__


def _ext_proxytype_from_spec_with(
    *,
    wrap_ext,
    bind,
    proxy_ref,
    module,
    name,
    methods,
    attrs,
    has_custom_getattr=False,
    has_instance_dict=False,
):
    spec = {
        '__module__': module,
    }

    cls = lookup(module, name)

    def unbound_function(name):
        return lambda instance, *args, **kwargs: getattr(instance, name)(*args, **kwargs)

    def proxy_method(name):
        if cls is not None and isinstance(cls, type) and hasattr(cls, name):
            return getattr(cls, name)

        return unbound_function(name)

    for method in methods:
        spec[method] = wrap_ext(proxy_method(method))

    spec['__getattr__'] = _generated_proxy_getattr(wrap_ext, cls, attrs, has_custom_getattr)
    spec['__setattr__'] = _generated_proxy_setattr(wrap_ext, cls, attrs, has_instance_dict)

    proxytype = type(name, (utils.ExternalWrapped, DynamicProxy,), spec)

    patchtype(module=module, name=name, cls=proxytype)
    bind(proxytype)
    bind(proxy_ref(proxytype))

    return proxytype


def int_proxy_factory(proxytype, bind):
    return functional.if_then_else(
        utils.is_wrapped,
        functional.identity,
        functional.compose(
            proxy(functional.memoize_one_arg(retrace.root_space.wrap(proxytype))),
            functional.side_effect(bind)))


class ProxytypeFactory:
    def __init__(self, *, internal, external, bind, is_patched_type, proxy_ref):
        self.internal = internal
        self.external = external
        self.bind = bind
        self.is_patched_type = is_patched_type
        self.proxy_ref = proxy_ref

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

    def int_proxytype(self, cls):
        assert not self.is_patched_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        spec = {}

        def wrap(func):
            return self._wrapped_function(self.internal.gateway, func)

        for name in superdict(cls).keys():
            if name not in blacklist:
                try:
                    value = getattr(cls, name)
                except AttributeError:
                    continue

                if utils.is_method_descriptor(value):
                    spec[name] = wrap(value)

        spec['__getattr__'] = wrap(getattr)
        spec['__setattr__'] = wrap(setattr)

        if utils.yields_callable_instances(cls):
            spec['__call__'] = self.internal.gateway

        spec['__class__'] = property(functional.constantly(cls))
        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__

        proxytype = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)
        self.bind(proxytype)
        patchtype(module=cls.__module__, name=cls.__qualname__, cls=proxytype)
        return proxytype

    def ext_proxytype(self, cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        methods = [method for method in method_names(cls) if method not in blacklist]
        attrs = [name for name in superdict(cls) if name not in blacklist]

        return _ext_proxytype_from_spec_with(
            wrap_ext=lambda target: self._wrapped_function(self.external.gateway, target),
            bind=self.bind,
            proxy_ref=self.proxy_ref,
            module=cls.__module__,
            name=cls.__qualname__,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=_has_custom_getattr(cls),
            has_instance_dict=_has_instance_dict(cls),
        )


def create_ext_proxytype_from_spec(int_gateway, ext_gateway, bind):
    proxy_ref = functional.memoize_one_arg(ProxyRef)

    def wrap_ext(target):
        wrapped = utils.wrapped_function(handler=ext_gateway, target=target)
        bind(wrapped)
        return wrapped

    def ext_proxytype_from_spec(
        *,
        module,
        name,
        methods,
        attrs,
        has_custom_getattr=False,
        has_instance_dict=False,
    ):
        spec = {
            '__module__': module,
        }

        cls = lookup(module, name)

        def unbound_function(name):
            return lambda instance, *args, **kwargs: getattr(instance, name)(*args, **kwargs)

        def proxy_method(name):
            if cls is not None and isinstance(cls, type) and hasattr(cls, name):
                return getattr(cls, name)

            return unbound_function(name)

        for method in methods:
            spec[method] = wrap_ext(proxy_method(method))

        spec['__getattr__'] = _generated_proxy_getattr(wrap_ext, cls, attrs, has_custom_getattr)
        spec['__setattr__'] = _generated_proxy_setattr(wrap_ext, cls, attrs, has_instance_dict)

        proxytype = type(name, (utils.ExternalWrapped, DynamicProxy,), spec)

        patchtype(module=module, name=name, cls=proxytype)
        bind(proxytype)
        bind(proxy_ref(proxytype))

        return proxytype

    bind(ext_proxytype_from_spec)

    return utils.wrapped_function(
        handler=int_gateway,
        target=ext_proxytype_from_spec,
    )
