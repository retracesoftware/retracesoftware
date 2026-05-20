from __future__ import annotations

from typing import Any, Callable

import retracesoftware.stream as stream
import retracesoftware.utils as utils

from retracesoftware.proxy.patchtype import patch_type, _module_unpatch_type


class TypePatcher:
    """Patch Python types so method calls route through a GatewayPair."""

    _patch_type_blacklist = frozenset(
        ["__new__", "__getattribute__", "__del__", "__dict__"]
    )

    def __init__(
        self,
        gateway_pair,
        *,
        bind: Callable[[Any], Any] = utils.noop,
        on_alloc: Callable[[Any], Any] = utils.noop,
        owner=None,
    ):
        self.owner = owner or self
        self.gateway_pair = gateway_pair
        self.is_bound = utils.WeakSet()
        self.bind = utils.runall(self.is_bound.add, bind)
        self.on_alloc = on_alloc
        self.patched_types = set()

    @property
    def ext_gateway(self):
        return self.gateway_pair.external

    @property
    def int_gateway(self):
        return self.gateway_pair.internal

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

    def descriptor_proxytype(self, cls):
        slots = {}

        for name in ["__get__", "__set__", "__delete__"]:
            if name in cls.__dict__:
                slots[name] = self._wrapped_function(self.ext_gateway, cls.__dict__[name])

        return type("DescriptorProxy", (utils.ExternalWrapped,), slots)

    def _on_alloc(self, obj):
        return self.on_alloc(obj)

    def patch_type(self, cls, install_session=None):
        return patch_type(self.owner, cls, install_session=install_session)

    def unpatch_type(self, cls):
        owner = self.owner
        tracked_types = []
        tracked_wrapped = []

        def visit(target):
            if getattr(target, "__retrace_system__", None) is not owner:
                return

            tracked_types.append(target)
            tracked_wrapped.extend(
                value
                for value in target.__dict__.values()
                if isinstance(value, utils._WrappedBase)
            )

            for subtype in target.__subclasses__():
                visit(subtype)

        visit(cls)
        _module_unpatch_type(cls)

        for target in tracked_types:
            stream.Binder.remove_bind_support(target)
            self.patched_types.discard(target)
            self.is_bound.discard(target)
            if owner is not self:
                owner.is_bound.discard(target)

        for wrapped in tracked_wrapped:
            self.is_bound.discard(wrapped)
            if owner is not self:
                owner.is_bound.discard(wrapped)

        return cls

    def unpatch_all(self):
        for cls in sorted(
            tuple(self.patched_types),
            key=lambda cls: len(cls.__mro__),
            reverse=True,
        ):
            if cls in self.patched_types:
                self.unpatch_type(cls)


def patch_type_for_gateway(cls, gateway_pair, *, bind=utils.noop, on_alloc=utils.noop):
    patcher = TypePatcher(gateway_pair, bind=bind, on_alloc=on_alloc)
    patcher.patch_type(cls)
    return patcher
