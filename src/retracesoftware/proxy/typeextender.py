from __future__ import annotations

from typing import Any, Callable

import retracesoftware.utils as utils

from retracesoftware.gateway._proxytype import DynamicProxy, superdict


_GENERATED_TYPE_BLACKLIST = frozenset((
    "__class__",
    "__dict__",
    "__getattribute__",
    "__hash__",
    "__init_subclass__",
    "__new__",
    "__weakref__",
))


def _method_value(value):
    if issubclass(type(value), utils.dispatch):
        value = utils.dispatch.table(value)["disabled"]

    if utils.is_method_descriptor(value) or (
        callable(value) and not isinstance(value, type)
    ):
        return value

    return None


def _method_items(cls, *, blacklist=_GENERATED_TYPE_BLACKLIST):
    for name in superdict(cls).keys():
        if name in blacklist:
            continue

        try:
            value = getattr(cls, name)
        except AttributeError:
            continue

        value = _method_value(value)
        if value is not None:
            yield name, value


def _replay_shape_method(cls, name):
    def method(*args, **kwargs):
        raise RuntimeError(
            f"replay shape method {cls.__qualname__}.{name} cannot execute live"
        )

    method.__name__ = name
    method.__qualname__ = f"{cls.__qualname__}.{name}"
    method.__retrace_shape_method__ = (cls, name)
    return method


def replay_shape_type(cls: type) -> type:
    """Return a root replay-only class that mirrors *cls*'s method shape.

    Replay for non-core external types should stay on the same ``extend_type``
    path as record where possible, but it does not need to inherit from the
    real third-party type.  This shape class is the input to that path: it has
    the same method names, but every shaped method including ``__init__`` raises
    if it is ever executed live.  During replay, the generated extended type
    wraps these methods and consumes recorded results instead of calling the
    raising bodies.
    """

    spec = {
        "__init__": _replay_shape_method(cls, "__init__"),
        "__module__": cls.__module__,
        "__qualname__": cls.__qualname__,
        "__retrace_original_type__": cls,
        "__retrace_shape_original_type__": cls,
        "__slots__": (),
    }

    for name, _value in _method_items(
        cls,
        blacklist=_GENERATED_TYPE_BLACKLIST | {"__del__", "__init__"},
    ):
        spec[name] = _replay_shape_method(cls, name)

    return type(cls.__name__, (object,), spec)


class TypeExtender:
    """Generate retrace-owned types without installing them into modules."""

    def __init__(
        self,
        *,
        on_new_instance: Callable[[Any], Any] = utils.noop,
        on_del: Callable[[Any], Any] = utils.noop,
        gateway_pair=None,
    ) -> None:
        self.on_new_instance = on_new_instance
        self.on_del = on_del
        self.gateway_pair = gateway_pair

    @property
    def ext_gateway(self):
        return self.gateway_pair.external

    @property
    def int_gateway(self):
        return self.gateway_pair.internal

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        return wrapped

    def _wrap_method_value(self, *, handler, value):
        value = _method_value(value)
        if value is None:
            return None

        return self._wrapped_function(handler, value)

    def _method_items(
        self,
        cls,
        *,
        blacklist=_GENERATED_TYPE_BLACKLIST,
    ):
        yield from _method_items(cls, blacklist=blacklist)

    def _generated_method_spec(
        self,
        cls,
        *,
        handler,
        blacklist=_GENERATED_TYPE_BLACKLIST,
    ):
        spec = {}

        for name, value in self._method_items(cls, blacklist=blacklist):
            spec[name] = self._wrap_method_value(handler=handler, value=value)

        return spec

    def _registering_init(self, wrapped_init):
        def __init__(instance, *args, **kwargs):
            return wrapped_init(instance, *args, **kwargs)

        return __init__

    def _generated_new(self, cls):
        on_new_instance = self.on_new_instance
        original_new = getattr(cls, "__new__", object.__new__)
        generated_type_ref = {}

        def __new__(subtype, *args, **kwargs):
            allocator = super(generated_type_ref["type"], subtype).__new__
            if original_new is object.__new__:
                instance = allocator(subtype)
            else:
                instance = allocator(subtype, *args, **kwargs)

            if not isinstance(instance, subtype):
                return instance

            object.__setattr__(instance, "_retrace_binding", None)
            object.__setattr__(instance, "_retrace_deleted", False)
            on_new_instance(instance)
            return instance

        return generated_type_ref, __new__

    def _wrap_required_init(self, cls, *, handler, spec):
        value = _method_value(getattr(cls, "__init__"))
        if value is None:
            return

        wrapped_init = self._wrapped_function(handler, value)
        spec["__init__"] = self._registering_init(wrapped_init)

    def extend_type(self, cls: type) -> type:
        on_del = self.on_del
        init_subclass_type_ref = {}
        new_type_ref, __new__ = self._generated_new(cls)
        original_type = getattr(cls, "__retrace_shape_original_type__", cls)
        subclass_override_names = frozenset(
            name for name, _value in self._method_items(
                cls,
                blacklist=_GENERATED_TYPE_BLACKLIST | {"__del__"},
            )
        )

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

            try:
                object.__setattr__(instance, "_retrace_binding", None)
            except Exception:
                pass

        def __retrace_binding__(instance):
            try:
                return object.__getattribute__(instance, "_retrace_binding")
            except AttributeError:
                return None

        def __retrace_serialized__(instance):
            return __retrace_binding__(instance)

        def __init_subclass__(subtype, **kwargs):
            super(init_subclass_type_ref["type"], subtype).__init_subclass__(**kwargs)

            # A retrace-extended object may be created inside the sandbox and
            # then handed to external code as the same Python object.  External
            # code only knows the original type's public method surface, so the
            # realistic ext->int callback risk is a subclass overriding one of
            # those known names.  Wrap only those overrides with the internal
            # gateway: inherited/base methods still go out through the external
            # gateway, while brand-new subclass-only helpers stay ordinary
            # Python methods instead of becoming unnecessary boundary surfaces.
            for name, value in tuple(subtype.__dict__.items()):
                if name not in subclass_override_names:
                    continue
                if name in _GENERATED_TYPE_BLACKLIST or name == "__del__":
                    continue

                wrapped = self._wrap_method_value(
                    handler=self.int_gateway,
                    value=value,
                )
                if wrapped is not None:
                    if name == "__init__":
                        wrapped = self._registering_init(wrapped)
                    setattr(subtype, name, wrapped)

        spec = self._generated_method_spec(
            cls,
            handler=self.ext_gateway,
            blacklist=_GENERATED_TYPE_BLACKLIST | {"__del__", "__init__"},
        )
        self._wrap_required_init(cls, handler=self.ext_gateway, spec=spec)
        spec.update({
            "__del__": __del__,
            "__init_subclass__": classmethod(__init_subclass__),
            "__module__": cls.__module__,
            "__new__": __new__,
            "__qualname__": cls.__qualname__,
            "__retrace_binding__": __retrace_binding__,
            "__retrace_serialized__": __retrace_serialized__,
            "__retrace_original_type__": original_type,
            "__slots__": ("_retrace_binding", "_retrace_deleted"),
        })

        retrace_type = type(cls.__name__, (cls,), spec)
        init_subclass_type_ref["type"] = retrace_type
        new_type_ref["type"] = retrace_type
        return retrace_type

    def wrap_type(self, cls: type) -> type:
        wrapper_type_ref = {}
        on_new_instance = self.on_new_instance
        on_del = self.on_del

        def __new__(wrapper_cls, *args, **kwargs):
            target = self.ext_gateway(cls, *args, **kwargs)
            target = utils.try_unwrap(target)
            instance = utils.create_wrapped(wrapper_cls, target)
            object.__setattr__(instance, "_retrace_binding", None)
            object.__setattr__(instance, "_retrace_deleted", False)
            on_new_instance(instance)
            return instance

        def __init__(instance, *args, **kwargs):
            pass

        def reported_class(instance):
            return wrapper_type_ref["type"]

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

            try:
                object.__setattr__(instance, "_retrace_binding", None)
            except Exception:
                pass

        def __retrace_binding__(instance):
            try:
                return object.__getattribute__(instance, "_retrace_binding")
            except AttributeError:
                return None

        def __retrace_serialized__(instance):
            return __retrace_binding__(instance)

        spec = self._generated_method_spec(
            cls,
            handler=self.ext_gateway,
        )
        spec.update({
            "__class__": property(reported_class),
            "__del__": __del__,
            "__init__": __init__,
            "__module__": cls.__module__,
            "__new__": __new__,
            "__qualname__": cls.__qualname__,
            "__retrace_binding__": __retrace_binding__,
            "__retrace_serialized__": __retrace_serialized__,
            "__retrace_original_type__": cls,
            "__slots__": ("_retrace_binding", "_retrace_deleted"),
            "__getattr__": self._wrapped_function(self.ext_gateway, getattr),
            "__setattr__": self._wrapped_function(self.ext_gateway, setattr),
        })

        wrapper_type = type(cls.__name__, (utils.ExternalWrapped, DynamicProxy), spec)
        wrapper_type_ref["type"] = wrapper_type
        return wrapper_type
