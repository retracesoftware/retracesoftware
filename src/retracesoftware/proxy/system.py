"""Gate-based proxy runtime state.

`System` owns the long-lived boundary state used by the proxy layer:

- the phase gate that distinguishes internal from external execution
- proxy/unproxy helpers and passthrough predicates
- patch/unpatch support for types and callables
- stable thread identity and install-time wiring

Mode-specific behavior such as recording, replay reads, checkpoints,
and callback/result hooks is assembled elsewhere and injected through
gateway factories plus lifecycle/hook attributes.
"""

from typing import NamedTuple, Callable, Any
from dataclasses import dataclass
from contextlib import contextmanager
import functools
import _thread
import retrace
import retracesoftware.functional as functional
import retracesoftware.stream as stream
import retracesoftware.utils as utils
import types
import gc
import sys

from retracesoftware.gateway._proxytype import method_names, superdict
from retracesoftware.gateway._proxytype import DynamicProxy
from retracesoftware.gateway import GatewayPair
from retracesoftware.install.edgecases import patchtype
from retracesoftware.install.monitoring import (
    begin_suppress_monitoring,
    end_suppress_monitoring,
)
from retracesoftware.proxy.gateway import ext_gateway, ext_method_gateway, int_gateway
from retracesoftware.proxy.patchtype import patch_type, _module_unpatch_type
from retracesoftware.proxy.typepatcher import TypePatcher
import retracesoftware.gateway._dynamicproxy as dynamicproxy
from retracesoftware.gateway._dynamicproxy import (
    ProxyRef,
    ProxytypeFactory,
    _ext_proxytype_from_spec_with,
    _has_custom_getattr,
    _has_instance_dict,
    create_ext_proxytype_from_spec,
    int_proxy_factory,
)
from retracesoftware.gateway._gatewaypair import (
    _active_space,
    _space_apply,
    _space_dispatch,
)


wrapped_callable = utils.wrapped_callable


class disabled_callable(wrapped_callable):
    """Callable that intentionally runs with retrace disabled."""

    __slots__ = ("_retrace_call",)

    def __new__(cls, wrapped, call):
        return super().__new__(cls, wrapped)

    def __init__(self, wrapped, call):
        self._retrace_call = call

    def __call__(self, *args, **kwargs):
        return self._retrace_call(*args, **kwargs)


def _wrap_retrace_disabled(function):
    return retrace.disable(function)


class LifecycleHooks(NamedTuple):
    on_start: Callable[..., Any] | None = None
    on_end: Callable[..., Any] | None = None

class CallHooks(NamedTuple):
    """Lifecycle callbacks for one side of the proxy boundary."""

    on_call: Callable[..., Any] | None = None
    on_result: Callable[..., Any] | None = None
    on_error: Callable[..., Any] | None = None

def when_instanceof(cls, on_then, on_else = functional.identity):
    return functional.if_then_else(functional.isinstanceof(cls), on_then, on_else)

def with_type_of(func):
    return functional.sequence(functional.typeof, func)

def _ext_proxytype_from_spec(
    system,
    module,
    name,
    methods,
    attrs,
    has_custom_getattr=False,
    has_instance_dict=False,
):
    return _ext_proxytype_from_spec_with(
        wrap_ext=lambda target: system._wrapped_function(handler=system.ext_gateway, target=target),
        bind=system.bind,
        proxy_ref=system.proxy_ref,
        module=module,
        name=name,
        methods=methods,
        attrs=attrs,
        has_custom_getattr=has_custom_getattr,
        has_instance_dict=has_instance_dict,
    )


fallback = functional.mapargs(transform = functional.walker(utils.try_unwrap), function = functional.apply)


def _phase_internal():
    return "internal"


def _phase_external():
    return "external"


def _phase_disabled():
    return None


class _GatewaySwitch:
    def __init__(self, system, expected, on_then, on_else):
        self.on_then = on_then
        self.on_else = on_else
        if expected == "internal":
            self._dispatch = system.create_dispatch(
                disabled=self._else,
                external=self._else,
                internal=self._then,
            )
        elif expected == "external":
            self._dispatch = system.create_dispatch(
                disabled=self._else,
                external=self._then,
                internal=self._else,
            )
        else:
            raise ValueError(f"unknown Retrace phase: {expected!r}")

    def _then(self, *args, **kwargs):
        return self.on_then(*args, **kwargs)

    def _else(self, *args, **kwargs):
        return self.on_else(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self._dispatch(*args, **kwargs)


@dataclass
class Endpoint:
    space: Any
    gateway: Any
    proxy: Callable[..., Any] | None = None


class ProxyFactory(NamedTuple):
    internal: Callable[..., Any] | None = None
    external: Callable[..., Any] | None = None


class Gateways(NamedTuple):
    internal: Endpoint
    external: Endpoint

    @classmethod
    def create(
        cls,
        *,
        proxy_factory=ProxyFactory(),
        internal_space=None,
        external_space=None,
        space_dispatch=None,
    ):
        space_dispatch = space_dispatch or retrace.space_dispatch
        return cls(
            internal=Endpoint(
                space=internal_space or retrace.CoordinateSpace(),
                gateway=space_dispatch(fallback),
                proxy=proxy_factory.internal,
            ),
            external=Endpoint(
                space=external_space or retrace.CoordinateSpace(),
                gateway=space_dispatch(fallback),
                proxy=proxy_factory.external,
            ),
        )

    def int_proxytype(self, cls):
        self.checkpoint(f'creating internal proxytype for {cls}')

        assert not self.is_patched_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        spec = {}

        def wrap(func): return self._wrapped_function(handler=self.internal.gateway, target=func)

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
            spec['__call__'] = self.int_gateway

        spec['__class__'] = property(functional.constantly(cls))
        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__

        proxytype = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)
        self.bind(proxytype)
        patchtype(module=cls.__module__, name=cls.__qualname__, cls=proxytype)
        return proxytype

    # def wrap_internal(self, fn):
    #     return utils.wrapped_function(
    #         handler=self.internal.gateway,
    #         target=fn,
    #     )

class BaseSystem:
    """Experimental shared proxy kernel built on retrace coordinate spaces."""

    def __init__(
        self,
        *,
        boundary_pair,
        on_bind=None,
    ):
        self._space_dispatch = getattr(retrace, "space_dispatch", _space_dispatch)
        self.disabled_space = retrace.disabled_space

        self.patched_types = set()
        self.immutable_types = set()
        self.is_bound = utils.WeakSet()
        self.bind = utils.runall(self.is_bound.add, on_bind)
        self.async_new_patched = utils.noop
        self.unwrap = functional.walker(utils.try_unwrap)
        self.proxy_ref = functional.memoize_one_arg(ProxyRef)
        self.create_stub_object = utils.runall(self.is_bound.add, utils.create_stub_object)
        self.checkpoint = utils.noop
        self.thread_id = _thread.get_ident

        self.is_patched_type = utils.FastTypePredicate(lambda cls: cls in self.patched_types).istypeof
        self.is_patched = lambda obj: (
            (isinstance(obj, type) and obj in self.patched_types)
            or type(obj) in self.patched_types
        )
        self.is_retraced = functional.or_predicate(self.is_bound, utils.is_wrapped)

        if boundary_pair.internal.proxy is None:
            boundary_pair.internal.proxy = functional.walker(self._proxy_int_value)
        if boundary_pair.external.proxy is None:
            boundary_pair.external.proxy = functional.walker(self._proxy_ext_value)
        self.boundary_pair = boundary_pair

        int_gateway = self.boundary_pair.internal.gateway
        self.ext_proxytype_from_spec = self._wrapped_function(int_gateway, _ext_proxytype_from_spec)
        self.descriptor_proxytype = functional.memoize_one_arg(self.descriptor_proxytype)
        self.int_proxytype = functional.memoize_one_arg(self.int_proxytype)
        self.ext_proxytype = functional.memoize_one_arg(self.ext_proxytype)
        self._current_phase = self.create_dispatch(
            disabled=_phase_disabled,
            external=_phase_external,
            internal=_phase_internal,
        )
        self.bind(self)

    @property
    def internal(self):
        return self.boundary_pair.internal

    @property
    def external(self):
        return self.boundary_pair.external

    @property
    def internal_space(self):
        return self.internal.space

    @property
    def external_space(self):
        return self.external.space

    @property
    def int_gateway(self):
        return self.internal.gateway

    @property
    def ext_gateway(self):
        return self.external.gateway

    @property
    def proxy_int(self):
        return self.internal.proxy

    @property
    def proxy_ext(self):
        return self.external.proxy

    def _space_for(self, phase):
        if phase == "internal":
            return self.internal.space
        if phase == "external":
            return self.external.space
        if phase is None:
            return self.disabled_space
        raise ValueError(f"unknown Retrace phase: {phase!r}")

    def apply_with(self, phase, function):
        return functional.partial(self._space_for(phase).apply, function)

    def run_internal(self, function, *args, **kwargs):
        return self.internal.space.apply(function, *args, **kwargs)

    def create_dispatch(self, *, disabled, external, internal):
        return self._space_dispatch(
            disabled,
            (
                (self.internal.space, internal),
                (self.external.space, external),
            ),
        )

    def enabled(self):
        return self.location is not None

    @property
    def location(self):
        return self._current_phase()

    def _is_immutable(self, obj):
        return isinstance(obj, tuple(self.immutable_types))

    def _should_passthrough_int(self, obj):
        return (
            self._is_immutable(obj)
            or isinstance(obj, utils.ExternalWrapped)
            or self.is_bound(obj)
        )

    def _should_passthrough_ext(self, obj):
        return (
            self._is_immutable(obj)
            or isinstance(obj, utils.ExternalWrapped)
            or isinstance(obj, utils.InternalWrapped)
            or self.is_bound(obj)
        )

    # Methods that must never be patched.  __new__ and __getattribute__
    # are fundamental to the object model; __del__ runs at GC time in
    # unpredictable contexts; __dict__ is a data descriptor needed by
    # the interpreter itself.
    _patch_type_blacklist = frozenset(['__new__', '__getattribute__', '__del__', '__dict__'])

    def _should_proxy_type(self, cls):
        return cls is not object and \
                not issubclass(cls, tuple(self.immutable_types)) and \
                cls not in (types.MemberDescriptorType, types.GetSetDescriptorType) and \
                cls not in self.patched_types

    def descriptor_proxytype(self, cls):
        slots = {}

        for name in ['__get__', '__set__', '__delete__']:
            if name in cls.__dict__:
                slots[name] = self._wrapped_function(self.ext_gateway, cls.__dict__[name])

        return type('FOOBAR', (utils.ExternalWrapped,), slots)

    def unpatch_type(self, cls):
        return self.type_patcher.unpatch_type(cls)

    def unpatch_types(self):
        return self.type_patcher.unpatch_all()

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

    def int_proxytype(self, cls):
        self.checkpoint(f'creating internal proxytype for {cls}')

        assert not self.is_patched_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        spec = {}

        def wrap(func): return self._wrapped_function(handler=self.int_gateway, target=func)

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
            spec['__call__'] = self.int_gateway

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
        has_custom_getattr = _has_custom_getattr(cls)
        has_instance_dict = _has_instance_dict(cls)

        return self.ext_proxytype_from_spec(
            self,
            module=cls.__module__,
            name=cls.__qualname__,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=has_custom_getattr,
            has_instance_dict=has_instance_dict,
        )

    def create_internal_proxy(self, obj):
        proxytype = self.int_proxytype(type(obj))
        wrapped = utils.create_wrapped(proxytype, obj)
        self.bind(wrapped)
        return wrapped

    def create_external_proxy(self, obj):
        proxytype = self.ext_proxytype(type(obj))
        return utils.create_wrapped(proxytype, obj)

    def _proxy_int_value(self, obj):
        if self._should_passthrough_int(obj):
            return obj
        if self.is_patched_type(obj):
            self.async_new_patched(obj)
            return obj
        return self.create_internal_proxy(obj)

    def _proxy_ext_value(self, obj):
        if self._should_passthrough_ext(obj):
            return obj
        if self.is_patched_type(obj):
            self.async_new_patched(obj)
            return obj
        return self.create_external_proxy(obj)

    def proxy_with(self, proxy):
        return functional.walker(proxy)


def wire_for_observation(
    boundary_pair,
    *,
    on_callback,
    on_error,
    on_result,
    is_passthrough,
):
    internal = boundary_pair.internal
    external = boundary_pair.external
    unwrap = functional.walker(utils.try_unwrap)

    ext_proxy = functional.walker(
        functional.if_then_else(
            is_passthrough,
            functional.identity,
            boundary_pair.external.proxy
        ))

    ext_result = functional.if_then_else(
        is_passthrough,
        functional.side_effect(on_result),
        functional.sequence(
            external.proxy,
            functional.side_effect(on_result),
            unwrap))

    callback_call = functional.mapargs(
        internal.space.apply,
        unwrap,
    )
    callback_runner = utils.observer(
        callback_call,
        on_call=on_callback,
    )

    external.gateway[internal.space] = functional.transform_call(
        external.space.apply,
        utils.try_unwrap,
        rest_transform=internal.proxy,
        result_transform=ext_result,
        on_error=on_error,
    )

    internal.gateway[external.space] = functional.transform_call(
        callback_runner,
        utils.try_unwrap,
        rest_transform=ext_proxy,
        result_transform=internal.proxy,
    )

    return boundary_pair

def create_proxy_factory(boundary_pair, *, on_bind):
    patched_types = set()
    immutable_types = set()
    is_bound = utils.WeakSet()
    bind = utils.runall(is_bound.add, on_bind)
    async_new_patched = utils.noop
    proxy_ref = functional.memoize_one_arg(ProxyRef)
    is_patched_type = utils.FastTypePredicate(lambda cls: cls in patched_types).istypeof
    proxytype_factory = ProxytypeFactory(
        internal=boundary_pair.internal,
        external=boundary_pair.external,
        bind=bind,
        is_patched_type=is_patched_type,
        proxy_ref=proxy_ref,
    )

    ext_passthrough = utils.FastTypePredicate(
        lambda cls: issubclass(cls, tuple(immutable_types)) or issubclass(cls, utils.InternalWrapped)
    ).istypeof
    int_passthrough = utils.FastTypePredicate(
        lambda cls: issubclass(cls, tuple(immutable_types)) or issubclass(cls, utils.ExternalWrapped)
    ).istypeof

    return ProxyFactory(
        internal=functional.walker(
            functional.if_then_else(
                int_passthrough,
                functional.identity,
                functional.if_then_else(
                    is_bound,
                    functional.identity,
                    functional.if_then_else(
                        is_patched_type,
                        functional.side_effect(async_new_patched),
                        functional.sequence(
                            dynamicproxy.proxy(functional.memoize_one_arg(proxytype_factory.int_proxytype)),
                            functional.side_effect(bind)))))),
        external=functional.walker(
            functional.if_then_else(
                ext_passthrough,
                functional.identity,
                functional.if_then_else(
                    is_bound,
                    functional.identity,
                    functional.if_then_else(
                        is_patched_type,
                        functional.side_effect(async_new_patched),
                        dynamicproxy.proxy(functional.memoize_one_arg(proxytype_factory.ext_proxytype)))))),
    )


def create_record_boundary_pair(
    *,
    internal : Endpoint,
    external : Endpoint,
    is_passthrough,
    on_callback,
    on_error,
    on_result,
    bind,
):
    """Create a record-time gateway pair from internal/external endpoints.

    ``internal`` and ``external`` are the endpoint objects being wired.
    ``is_passthrough`` is a predicate called with candidate result values.  It
    returns ``True`` when a value can cross unchanged, and ``False`` when the
    value must first be converted through the external proxy path.
    ``on_callback`` observes external-to-internal callback invocation.
    ``on_error`` observes external-call errors without replacing the exception.
    ``on_result`` observes external-call results after record-side proxying.
    ``bind`` records objects that must be known to the binding layer,
    including created proxy types and internal proxy instances.
    """
    pair = Gateways(internal=internal, external=external)

    proxied = internal.space.wrap(create_ext_proxytype_from_spec(
        int_gateway=internal.gateway,
        ext_gateway=external.gateway,
        bind = bind
    ))

    def ext_proxytype(cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        methods = [method for method in method_names(cls) if method not in blacklist]
        attrs = [name for name in superdict(cls) if name not in blacklist]

        return proxied(
            module=cls.__module__,
            name=cls.__qualname__,
            methods=methods,
            attrs=attrs,
        )

    pair.external.proxy = dynamicproxy.proxy(retrace.root_space.wrap(ext_proxytype))

    proxytype_factory = ProxytypeFactory(
        internal=pair.internal,
        external=pair.external,
        bind=bind,
        is_patched_type=utils.FastTypePredicate(lambda cls: False).istypeof,
        proxy_ref=functional.memoize_one_arg(ProxyRef),
    )

    pair.internal.proxy = int_proxy_factory(
        proxytype = retrace.root_space.wrap(
            functional.compose(
                proxytype_factory.int_proxytype,
                functional.side_effect(bind))),
        bind = bind)

    wire_for_observation(
        pair,
        on_callback=on_callback,
        on_error=on_error,
        on_result=on_result,
        is_passthrough=is_passthrough,
    )
    return pair

def wire_for_replay(
    boundary_pair,
    *,
    next_result,
):
    internal = boundary_pair.internal
    external = boundary_pair.external
    unwrap = functional.walker(utils.try_unwrap)

    external.gateway[internal.space] = functional.transform_call(
        external.space.apply,
        functional.constantly(next_result),
        rest_transform=internal.proxy,
        result_transform=unwrap,
    )

    internal.gateway[external.space] = functional.transform_call(
        internal.space.apply,
        utils.try_unwrap,
        rest_transform=unwrap,
        result_transform=internal.proxy,
    )

    return boundary_pair

def create_replay_boundary_pair(
    *,
    internal,
    external,
    is_passthrough,
    next_result,
    bind,
):
    """Create a replay-time gateway pair from internal/external endpoints."""
    pair = Gateways(internal=internal, external=external)

    pair.internal.space.wrap(create_ext_proxytype_from_spec(
        int_gateway=pair.internal.gateway,
        ext_gateway=pair.external.gateway,
        bind = bind
    ))

    def illegal_external_proxy(value):
        raise RuntimeError("replay cannot create external proxies from live values")

    pair.external.proxy = illegal_external_proxy

    proxytype_factory = ProxytypeFactory(
        internal=pair.internal,
        external=pair.external,
        bind=bind,
        is_patched_type=utils.FastTypePredicate(lambda cls: False).istypeof,
        proxy_ref=functional.memoize_one_arg(ProxyRef),
    )

    pair.internal.proxy = int_proxy_factory(
        proxytype = retrace.root_space.wrap(
            functional.compose(
                proxytype_factory.int_proxytype,
                functional.side_effect(bind))),
        bind = bind)

    wire_for_replay(pair, next_result = next_result)

    return pair



class ObservableSystem(BaseSystem):
    """Experimental live boundary behavior, isolated from the active System path."""

    def __init__(self,
                 *,
                 boundary_pair,
                 on_callback=utils.noop,
                 on_error=utils.noop,
                 on_result=utils.noop,
                 on_bind=None):
        super().__init__(
            boundary_pair=boundary_pair,
            on_bind=on_bind,
        )

        self.on_callback = on_callback
        self.on_error = on_error
        self.on_result = on_result

        wire_for_observation(
            self.boundary_pair,
            on_callback=on_callback,
            on_error=on_error,
            on_result=on_result,
            is_passthrough=self._should_passthrough_ext,
        )




class ReplaySystem(BaseSystem):
    def __init__(self,
                 next_result,
                 *,
                 boundary_pair,
                 on_bind=None):

        super().__init__(
            boundary_pair=boundary_pair,
            on_bind=on_bind,
        )

        wire_for_replay(
            self.boundary_pair,
            next_result=next_result,
        )



class System:
    """Mutable runtime state shared by record and replay assemblers."""

    def patch_function(self, fn):
        """Return a wrapper that routes *fn* through the external gate.

        Use this for standalone module-level functions (e.g.
        ``time.time``, ``os.getpid``) that need to be recorded and
        replayed.
        """
        if not self.is_bound(fn):
            self.bind(fn)
        wrapped = self._wrapped_function(self.ext_gateway, fn)
        standalone = wrapped_callable(wrapped)
        self.bind(standalone)
        return standalone

    def ext_proxy_result(self, fn):
        """Return a wrapper that live-runs *fn* and proxies its result.

        ``ext_proxy_result`` is for local runtime factories whose returned object
        must enter Retrace's binding/proxy world, but whose call itself should
        run in both record and replay rather than being replayed from a recorded
        ``RESULT``. These factories must not call back into retraced Python on
        the current thread, and their inputs must already be ordinary unwrapped
        values. They also must not allocate through patched Python constructors
        before the result reaches ``ext_proxy``.
        """
        call_real = fn
        ext_proxy_result = functional.sequence(call_real, self.ext_proxy)
        wrapped = self.create_dispatch(
            disabled=call_real,
            external=ext_proxy_result,
            internal=ext_proxy_result,
        )

        standalone = wrapped_callable(wrapped)
        self.bind(standalone)
        return standalone

    def patch(self, obj, install_session=None):
        """Patch *obj* for proxying — dispatches by type.

        If *obj* is a class, delegates to module-level ``patch_type``
        (mutates the
        class in-place, returns ``None``).

        If *obj* is a callable (function, builtin, etc.), delegates to
        ``patch_function`` (returns a new ``BoundGate`` wrapper).

        Raises ``TypeError`` for anything else.
        """
        if isinstance(obj, type):
            self.type_patcher.patch_type(obj, install_session=install_session)
            return obj
        if callable(obj):
            return self.patch_function(obj)
        raise TypeError(f"cannot patch {type(obj).__name__!r} object")

    def disable_for(self, function, *, unwrap_args=True, retrace=True):
        """Return a callable that runs *function* with the phase gate cleared.

        This is used when the system needs to call its own internal
        helpers (e.g. proxytype factories) without triggering the
        adapter pipeline or retrace-python coordinate tracking.

        ``function`` may itself be a ``wrapped_function``. In that case we
        still want to execute its underlying target rather than re-entering
        the wrapper/handler path with the gate cleared.

        When ``unwrap_args`` is true, the disabled call also recursively
        unwraps nested args/kwargs through ``fallback``. When false, it
        only unwraps the callable itself and passes args/kwargs through
        unchanged via ``utils.try_unwrap_apply``.

        ``retrace`` controls whether the callable is also hidden from
        retrace-python coordinates. Control-plane callers use the default;
        module interception config uses ``False`` for application/library
        passthroughs that should only bypass the proxy gate.
        """
        disabled = fallback if unwrap_args else utils.try_unwrap_apply
        applied = self.apply_with(None, functional.partial(disabled, function))
        if retrace:
            return _wrap_retrace_disabled(disabled_callable(function, applied))
        return disabled_callable(function, applied)

    def disabled_method_for(self, function, *, retrace=True):
        """Disable a method while preserving descriptor binding."""
        disabled_function = self.disable_for(
            function,
            unwrap_args=False,
            retrace=retrace,
        )

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return disabled_function(*args, **kwargs)

        return wrapper

    def __init__(self, *, gateway_pair, on_bind=None, bind=None, internal_space=None) -> None:
        if not hasattr(self, "is_bound"):
            self._init_common(on_bind=on_bind, bind=bind, internal_space=internal_space)
        self._install_gateway_pair(gateway_pair)

    def _init_common(self, *, on_bind=None, bind=None, internal_space=None) -> None:
        self._space_dispatch = getattr(retrace, "space_dispatch", _space_dispatch)
        self.disabled_space = retrace.disabled_space
        self._requested_internal_space = internal_space

        self.lifecycle_hooks = LifecycleHooks(
            on_start=None,
            on_end=None,
        )
        self.primary_hooks = CallHooks()
        self.secondary_hooks = CallHooks()

        self.immutable_types = set()
        self.is_bound = utils.WeakSet()
        self.bind = utils.runall(self.is_bound.add, bind, on_bind)

        self.async_new_patched = utils.noop
        self.write_callback = utils.noop
        self.write_callback_result = utils.noop
        self.write_callback_error = utils.noop

        self.create_stub_object = utils.runall(self.is_bound.add, utils.create_stub_object)
        self.is_retraced = functional.or_predicate(self.is_bound, utils.is_wrapped)
        self.checkpoint = utils.noop
        self.thread_id = _thread.get_ident
        self.proxy_ref = functional.memoize_one_arg(ProxyRef)

    def _install_gateway_pair(self, gateway_pair) -> None:
        self.gateway_pair = gateway_pair
        self.internal_space = self.gateway_pair.sandbox_space
        self.external_space = self.gateway_pair._external_space
        self.int_gateway = self.gateway_pair.internal
        self.ext_gateway = self.gateway_pair.external
        self.ext_method_gateway = self.ext_gateway

        self._current_phase = self._space_dispatch(
            _phase_disabled,
            (
                (self.internal_space, _phase_internal),
                (self.external_space, _phase_external),
            ),
        )

        self.type_patcher = TypePatcher(
            self.gateway_pair,
            bind=self.bind,
            on_alloc=self._on_alloc,
            owner=self,
        )
        self.patched_types = self.type_patcher.patched_types
        self.is_patched = lambda obj: (
            (isinstance(obj, type) and obj in self.patched_types)
            or type(obj) in self.patched_types
        )
        self.is_patched_type = utils.FastTypePredicate(lambda cls: cls in self.patched_types).istypeof

        self.serialize_ext_wrapped = functional.walker(
            when_instanceof(utils.ExternalWrapped, with_type_of(self.proxy_ref)))

        self.ext_proxytype_from_spec = self._wrapped_function(
            self.int_gateway,
            _ext_proxytype_from_spec,
        )
        self.descriptor_proxytype = functional.memoize_one_arg(self.descriptor_proxytype)
        self.bind(self)

    def _is_immutable(self, obj):
        return isinstance(obj, tuple(self.immutable_types))

    def _should_passthrough_int(self, obj):
        return (
            self._is_immutable(obj)
            or isinstance(obj, utils.ExternalWrapped)
            or self.is_bound(obj)
        )

    def _should_passthrough_ext(self, obj):
        return (
            self._is_immutable(obj)
            or isinstance(obj, utils.InternalWrapped)
            or self.is_bound(obj)
        )



    @property
    def ext_proxy(self):
        ext_passthrough = utils.FastTypePredicate(
            lambda cls: issubclass(cls, tuple(self.immutable_types)) or issubclass(cls, utils.InternalWrapped)
        ).istypeof

        return functional.walker(
            functional.if_then_else(
                ext_passthrough,
                functional.identity,
                functional.if_then_else(
                    self.is_bound,
                    functional.identity,
                    functional.if_then_else(
                        self.is_patched_type,
                        functional.side_effect(self.async_new_patched),
                        dynamicproxy.proxy(functional.memoize_one_arg(self.ext_proxytype))))))

    @property
    def int_proxy(self):
        int_passthrough = utils.FastTypePredicate(
            lambda cls: issubclass(cls, tuple(self.immutable_types)) or issubclass(cls, utils.ExternalWrapped)
        ).istypeof

        return functional.walker(
            functional.if_then_else(
                int_passthrough,
                functional.identity,
                functional.if_then_else(
                    self.is_bound,
                    functional.identity,
                    functional.if_then_else(
                        self.is_patched_type,
                        functional.side_effect(self.async_new_patched),
                        functional.sequence(
                            dynamicproxy.proxy(functional.memoize_one_arg(self.int_proxytype)),
                            functional.side_effect(self.bind))))))


    def wrap_async(self, function):
        return self._wrapped_function(self.int_gateway, function)

    def _call_bind(self, obj):
        return self.bind(obj)

    def _call_async_new_patched(self, obj):
        return self.async_new_patched(obj)

    def _on_alloc(self, obj):
        if getattr(_active_space, "current", None) is self.disabled_space:
            return None
        if self.location == "external":
            return self.async_new_patched(obj)
        return self.bind(obj)

    def _space_for(self, phase):
        if phase == "internal":
            return self.internal_space
        if phase == "external":
            return self.external_space
        if phase is None:
            return self.disabled_space
        raise ValueError(f"unknown Retrace phase: {phase!r}")

    def apply_with(self, phase, function):
        return functional.partial(_space_apply, self._space_for(phase), function)

    def run_internal(self, function, *args, **kwargs):
        return _space_apply(self.internal_space, function, *args, **kwargs)

    def create_dispatch(self, *, disabled, external, internal):
        """Dispatch by current Retrace coordinate space."""
        return self._space_dispatch(
            disabled,
            (
                (self.internal_space, internal),
                (self.external_space, external),
            ),
        )

    def enabled(self):
        return self.location is not None

    @contextmanager
    def enable(self):
        gc.collect()
        try:
            if callable(self.lifecycle_hooks.on_start):
                self.lifecycle_hooks.on_start()
            yield
        finally:
            if callable(self.lifecycle_hooks.on_end):
                self.lifecycle_hooks.on_end()

    def run(self, function, *args, **kwargs):
        previous_count = begin_suppress_monitoring()
        try:
            manager = self.enable()
            manager.__enter__()
        finally:
            end_suppress_monitoring(previous_count)
        try:
            result = self.run_internal(function, *args, **kwargs)
        except BaseException:
            exc_info = sys.exc_info()
            previous_count = begin_suppress_monitoring()
            try:
                suppress = manager.__exit__(*exc_info)
            finally:
                end_suppress_monitoring(previous_count)
            if not suppress:
                raise
        else:
            previous_count = begin_suppress_monitoring()
            try:
                manager.__exit__(None, None, None)
            finally:
                end_suppress_monitoring(previous_count)
            return result

    @property
    def location(self):
        return self._current_phase()


    # Methods that must never be patched.  __new__ and __getattribute__
    # are fundamental to the object model; __del__ runs at GC time in
    # unpredictable contexts; __dict__ is a data descriptor needed by
    # the interpreter itself.
    _patch_type_blacklist = frozenset(['__new__', '__getattribute__', '__del__', '__dict__'])

    def _should_proxy_type(self, cls):
        """Decide whether values of *cls* need a dynamic proxy wrapper.

        Returns False for:
          - ``object`` itself (everything is an object, skip it)
          - any subclass of a type in ``immutable_types`` (e.g. int,
            str, bytes — their values pass through the boundary as-is)
          - built-in C data descriptors used by ``wrapped_member``
            (their raw descriptor object is part of the call shape)
          - any type already in ``patched_types`` (already handled)
        """
        return cls is not object and \
                not issubclass(cls, tuple(self.immutable_types)) and \
                cls not in (types.MemberDescriptorType, types.GetSetDescriptorType) and \
                cls not in self.patched_types

    def descriptor_proxytype(self, cls):
        slots = {}

        for name in ['__get__', '__set__', '__delete__']:
            if name in cls.__dict__:
                slots[name] = self._wrapped_function(self.ext_gateway, cls.__dict__[name])

        return type('FOOBAR', (utils.ExternalWrapped,), slots)

    def unpatch_type(self, cls):
        return self.type_patcher.unpatch_type(cls)

    def unpatch_types(self):
        return self.type_patcher.unpatch_all()

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler = handler, target = target)
        self.bind(wrapped)
        return wrapped

    def _wrapped_method(self, target):
        return self._wrapped_function(self.ext_method_gateway, target)

    def int_proxytype(self, cls):

        self.checkpoint(f'creating internal proxytype for {cls}')

        assert not self.is_patched_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

        spec = {}

        def wrap(func): return self._wrapped_function(handler = self.int_gateway, target = func)

        for name in superdict(cls).keys():
            if name not in blacklist:
                try:
                    value = getattr(cls, name)
                except AttributeError:
                    # Some metatype attributes listed in the MRO dicts are not
                    # readable on the concrete class (for example `type` exposes
                    # `__abstractmethods__` here on 3.12). Skip those slots.
                    continue

                if utils.is_method_descriptor(value):
                    spec[name] = wrap(value)

        spec['__getattr__'] = wrap(getattr)
        spec['__setattr__'] = wrap(setattr)

        if utils.yields_callable_instances(cls):
            spec['__call__'] = self.int_gateway

        spec['__class__'] = property(functional.constantly(cls))

        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__

        proxytype = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)

        self.bind(proxytype)

        patchtype(module = cls.__module__, name = cls.__qualname__, cls = proxytype)

        return proxytype

    def ext_proxytype(self, cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

        methods = [method for method in method_names(cls) if method not in blacklist]
        attrs = [name for name in superdict(cls) if name not in blacklist]
        has_custom_getattr = _has_custom_getattr(cls)
        has_instance_dict = _has_instance_dict(cls)

        return self.ext_proxytype_from_spec(
            self,
            module=cls.__module__,
            name=cls.__qualname__,
            methods=methods,
            attrs=attrs,
            has_custom_getattr=has_custom_getattr,
            has_instance_dict=has_instance_dict,
        )

    def install(self):
        def uninstall():
            if getattr(self, "retrace_mode", None) == "replay":
                self._replay_trace_active = False

        return uninstall


class RecordSystem(System):
    """Record-time proxy system backed by a GatewayPair."""

    def __init__(self, *, writer=None, on_bind=None, bind=None, internal_space=None):
        self.writer = writer
        self._init_common(on_bind=on_bind, bind=bind, internal_space=internal_space)
        gateway_pair = GatewayPair.create_recording_pair(
            is_passthrough=self._should_passthrough_ext,
            on_callback=lambda *args, **kwargs: self._call_primary("on_call", *args, **kwargs),
            on_error=lambda *args, **kwargs: self._call_primary("on_error", *args, **kwargs),
            on_result=lambda *args, **kwargs: self._call_primary("on_result", *args, **kwargs),
            bind=self.bind,
        )
        super().__init__(
            gateway_pair=gateway_pair,
            on_bind=on_bind,
            bind=bind,
            internal_space=internal_space,
        )
        self.retrace_mode = "record"

    def _call_primary(self, hook_name, *args, **kwargs):
        hook = getattr(self.primary_hooks, hook_name)
        if hook is not None:
            return hook(*args, **kwargs)
        return None


class _LegacyGatewayPairAdapter:
    def __init__(self, pair):
        self.internal = pair.internal.gateway
        self.external = pair.external.gateway
        self._sandbox_space = pair.internal.space
        self._external_space = pair.external.space

    @property
    def sandbox_space(self):
        return self._sandbox_space


class ReplaySystem(System):
    """Replay-time proxy system backed by a GatewayPair."""

    def __init__(
        self,
        next_result=None,
        *,
        boundary_pair=None,
        gateway_pair=None,
        on_bind=None,
        bind=None,
        internal_space=None,
    ):
        self.next_result = next_result or self._missing_next_result
        self._skip_wrapper_bind = False
        self._init_common(on_bind=on_bind, bind=bind, internal_space=internal_space)
        if boundary_pair is not None and gateway_pair is None:
            wire_for_replay(boundary_pair, next_result=self._read_next_result)
            gateway_pair = _LegacyGatewayPairAdapter(boundary_pair)
        if gateway_pair is None:
            gateway_pair = GatewayPair.create_replay_pair(
                is_passthrough=self._should_passthrough_ext,
                next_result=self._read_next_result,
                bind=self.bind,
            )
        super().__init__(
            gateway_pair=gateway_pair,
            on_bind=on_bind,
            bind=bind,
            internal_space=internal_space,
        )
        self.retrace_mode = "replay"
        self._replay_trace_active = True

    def _missing_next_result(self, *args, **kwargs):
        raise RuntimeError("ReplaySystem.next_result has not been configured")

    def _read_next_result(self, *args, **kwargs):
        return self.next_result(*args, **kwargs)

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        if self._skip_wrapper_bind:
            self.is_bound.add(wrapped)
        else:
            self.bind(wrapped)
        return wrapped

    def ext_proxytype(self, cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
        methods = [method for method in method_names(cls) if method not in blacklist]
        attrs = [name for name in superdict(cls) if name not in blacklist]
        has_custom_getattr = _has_custom_getattr(cls)
        has_instance_dict = _has_instance_dict(cls)

        def bind_generated(obj):
            self.is_bound.add(obj)
            return obj

        self._skip_wrapper_bind = True
        try:
            return _ext_proxytype_from_spec_with(
                wrap_ext=lambda target: self._wrapped_function(
                    handler=self.ext_gateway,
                    target=target,
                ),
                bind=bind_generated,
                proxy_ref=self.proxy_ref,
                module=cls.__module__,
                name=cls.__qualname__,
                methods=methods,
                attrs=attrs,
                has_custom_getattr=has_custom_getattr,
                has_instance_dict=has_instance_dict,
            )
        finally:
            self._skip_wrapper_bind = False
