import sys

import retrace
import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.gateway._proxytype import DynamicProxy


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


def _serialized_external_proxy(instance):
    return getattr(type(instance), "__retrace_type_binding__", None)


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
    customize_proxy_type=utils.noop,
    has_custom_getattr=False,
    has_instance_dict=False,
    reported_class=None,
    source_class=None,
):
    spec = {
        '__module__': module,
    }

    if reported_class is not None:
        spec['__class__'] = property(functional.constantly(reported_class))

    cls = source_class or lookup(module, name)
    target_class = getattr(cls, "__retrace_target_class__", cls)
    if cls is not None:
        spec['__retrace_target_class__'] = cls

    def unbound_function(name):
        return lambda instance, *args, **kwargs: getattr(instance, name)(*args, **kwargs)

    def proxy_method(name):
        if (
            target_class is not None
            and isinstance(target_class, type)
            and hasattr(target_class, name)
        ):
            return getattr(target_class, name)

        return unbound_function(name)

    wrapped_new = wrap_ext(proxy_method("__new__"))
    wrapped_init = (
        wrap_ext(proxy_method("__init__"))
        if "__init__" in methods
        else None
    )

    def __new__(proxy_cls, *args, **kwargs):
        target = wrapped_new(proxy_cls, *args, **kwargs)
        target = utils.try_unwrap(target)
        return utils.create_wrapped(proxy_cls, target)

    def __init__(instance, *args, **kwargs):
        if wrapped_init is None:
            return None

        return wrapped_init(instance, *args, **kwargs)

    for method in methods:
        if method in {"__new__", "__init__"}:
            continue
        spec[method] = wrap_ext(proxy_method(method))

    spec['__new__'] = __new__
    spec['__init__'] = __init__
    spec['__slots__'] = ()
    spec['__getattr__'] = _generated_proxy_getattr(wrap_ext, cls, attrs, has_custom_getattr)
    spec['__setattr__'] = _generated_proxy_setattr(wrap_ext, cls, attrs, has_instance_dict)
    spec['__retrace_serialized__'] = _serialized_external_proxy

    proxytype = type(name, (utils.ExternalWrapped, DynamicProxy,), spec)

    customize_proxy_type(module=module, name=name, cls=proxytype)
    proxytype.__retrace_type_binding__ = bind(proxytype)
    bind(proxy_ref(proxytype))

    return proxytype


def int_proxy_factory(proxytype, bind):
    wrap = proxy(functional.memoize_one_arg(retrace.root_space.wrap(proxytype)))

    def create(value):
        if utils.is_wrapped(value):
            return value

        proxied = wrap(value)
        bind(proxied)
        try:
            object.__setattr__(proxied, "_retrace_deleted", False)
        except Exception:
            pass
        return proxied

    return create


def create_ext_proxytype_from_spec(
    int_gateway,
    ext_gateway,
    bind,
    customize_proxy_type=utils.noop,
):
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
        reported_class=None,
        source_class=None,
    ):
        return _ext_proxytype_from_spec_with(
            wrap_ext=wrap_ext,
            bind=bind,
            proxy_ref=proxy_ref,
            customize_proxy_type=customize_proxy_type,
            module=module,
            name=name,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=has_custom_getattr,
            has_instance_dict=has_instance_dict,
            reported_class=reported_class,
            source_class=source_class,
        )

    bind(ext_proxytype_from_spec)

    return utils.wrapped_function(
        handler=int_gateway,
        target=ext_proxytype_from_spec,
    )
