from __future__ import annotations

from typing import Any, Callable, NamedTuple, Protocol
import threading

import retrace

from retracesoftware import utils
from retracesoftware import functional
import retracesoftware.gateway._dynamicproxy as dynamicproxy
from retracesoftware.gateway._dynamicproxy import (
    ProxyRef,
    ProxytypeFactory,
    create_ext_proxytype_from_spec,
    int_proxy_factory,
)
from retracesoftware.gateway._proxytype import method_names, superdict


PassthroughPredicate = Callable[[Any], bool]
BindCallback = Callable[[Any], Any]
ResultCallback = Callable[[Any], Any]
ErrorCallback = Callable[[type, BaseException, Any], Any]
NextResult = Callable[..., Any]


class CallbackObserver(Protocol):
    def __call__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        ...


class Endpoint(NamedTuple):
    space: Any
    gateway: Any
    proxy: Callable[..., Any] | None = None


fallback = functional.mapargs(transform=functional.walker(utils.try_unwrap), function=functional.apply)
_active_space = threading.local()


def _space_apply(space, function, *args, **kwargs):
    previous = getattr(_active_space, "current", None)
    _active_space.current = space
    try:
        apply = getattr(space, "apply", None)
        if apply is not None:
            return apply(function, *args, **kwargs)
        run = getattr(space, "run", None)
        if run is not None:
            return run(function, *args, **kwargs)
        return function(*args, **kwargs)
    finally:
        _active_space.current = previous


class _SpaceDispatch:
    def __init__(self, default, cases=()):
        self.default = default
        self.mapping = {}
        for space, function in cases:
            self[space] = function

    def _key(self, space):
        return getattr(space, "id", space)

    def __getitem__(self, space):
        return self.mapping[self._key(space)]

    def __setitem__(self, space, function):
        self.mapping[self._key(space)] = function

    def __call__(self, *args, **kwargs):
        space = getattr(_active_space, "current", None)
        function = self.mapping.get(self._key(space), self.default)
        return function(*args, **kwargs)


def _space_dispatch(default, cases=()):
    dispatch = getattr(retrace, "space_dispatch", None)
    if dispatch is not None:
        return dispatch(default, cases)
    return _SpaceDispatch(default, cases)


def _space_apply_callable(space):
    return functional.partial(_space_apply, space)


def _space_wrap(space, function):
    def wrapped(*args, **kwargs):
        return _space_apply(space, function, *args, **kwargs)

    return wrapped


def create_endpoint(space=None) -> Endpoint:
    return Endpoint(
        space=space or retrace.CoordinateSpace(),
        gateway=_space_dispatch(fallback),
    )

def _wire_for_observation(
    *,
    internal: Endpoint,
    external: Endpoint,
    on_callback: CallbackObserver,
    on_error: ErrorCallback,
    on_result: ResultCallback,
    is_passthrough: PassthroughPredicate,
) -> None:
    unwrap = functional.walker(utils.try_unwrap)
    external_arg = functional.if_then_else(
        utils.is_wrapped,
        utils.try_unwrap,
        internal.proxy,
    )

    ext_proxy = functional.walker(
        functional.if_then_else(
            is_passthrough,
            functional.identity,
            external.proxy,
        ))

    ext_result = functional.if_then_else(
        is_passthrough,
        functional.side_effect(on_result),
        functional.sequence(
            external.proxy,
            functional.side_effect(on_result),
            unwrap))

    callback_call = functional.mapargs(
        _space_apply_callable(internal.space),
        unwrap,
    )
    callback_runner = utils.observer(
        callback_call,
        on_call=on_callback,
    )

    external.gateway[internal.space] = functional.transform_call(
        _space_apply_callable(external.space),
        utils.try_unwrap,
        rest_transform=external_arg,
        result_transform=ext_result,
        on_error=on_error,
    )

    internal.gateway[external.space] = functional.transform_call(
        callback_runner,
        utils.try_unwrap,
        rest_transform=ext_proxy,
        result_transform=internal.proxy,
    )


def _wire_for_replay(
    *,
    internal: Endpoint,
    external: Endpoint,
    next_result: NextResult,
) -> None:
    unwrap = functional.walker(utils.try_unwrap)
    external_arg = functional.if_then_else(
        utils.is_wrapped,
        utils.try_unwrap,
        internal.proxy,
    )

    external.gateway[internal.space] = functional.transform_call(
        _space_apply_callable(external.space),
        functional.constantly(next_result),
        rest_transform=external_arg,
        result_transform=unwrap,
    )

    internal.gateway[external.space] = functional.transform_call(
        _space_apply_callable(internal.space),
        utils.try_unwrap,
        rest_transform=unwrap,
        result_transform=internal.proxy,
    )

class GatewayPair:
    __slots__ = ("internal", "external", "_sandbox_space", "_external_space")

    def __init__(
        self,
        *,
        internal: Callable[..., Any],
        external: Callable[..., Any],
        sandbox_space: Any,
        external_space: Any,
    ) -> None:
        self.internal = internal
        self.external = external
        self._sandbox_space = sandbox_space
        self._external_space = external_space

    @property
    def sandbox_space(self) -> Any:
        return self._sandbox_space

    @staticmethod
    def create_recording_pair(
        *,
        is_passthrough: PassthroughPredicate,
        on_callback: CallbackObserver,
        on_error: ErrorCallback,
        on_result: ResultCallback,
        bind: BindCallback,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
        internal_space=None,
        external_space=None,
    ) -> GatewayPair:
        """Create record-time gateways.

        ``is_passthrough`` is a predicate called with values crossing back to
        external code.  It returns ``True`` when that value is already safe to
        pass through unchanged, and ``False`` when GatewayPair must proxy it.
        """
        internal = create_endpoint(internal_space)
        external = create_endpoint(external_space)

        proxied = _space_wrap(internal.space, create_ext_proxytype_from_spec(
            int_gateway=internal.gateway,
            ext_gateway=external.gateway,
            bind=bind,
            customize_proxy_type=proxy_type_customizer,
        ))

        def ext_proxytype(cls: type) -> type:
            blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']
            methods = [method for method in method_names(cls) if method not in blacklist]
            attrs = [name for name in superdict(cls) if name not in blacklist]

            return proxied(
                module=cls.__module__,
                name=cls.__qualname__,
                methods=methods,
                attrs=attrs,
            )

        external = external._replace(
            proxy=dynamicproxy.proxy(retrace.root_space.wrap(ext_proxytype)),
        )

        proxytype_factory = ProxytypeFactory(
            internal=internal,
            external=external,
            bind=bind,
            is_patched_type=utils.FastTypePredicate(lambda cls: False).istypeof,
            proxy_ref=functional.memoize_one_arg(ProxyRef),
            customize_proxy_type=proxy_type_customizer,
        )
        internal_proxy = int_proxy_factory(
            proxytype=retrace.root_space.wrap(
                functional.compose(
                    proxytype_factory.int_proxytype,
                    functional.side_effect(bind))),
            bind=bind)
        internal = internal._replace(
            proxy=functional.if_then_else(
                is_passthrough,
                functional.identity,
                internal_proxy,
            ),
        )

        _wire_for_observation(
            internal=internal,
            external=external,
            on_callback=on_callback,
            on_error=on_error,
            on_result=on_result,
            is_passthrough=is_passthrough,
        )
        return GatewayPair(
            internal=_space_wrap(external.space, internal.gateway),
            external=_space_wrap(internal.space, external.gateway),
            sandbox_space=internal.space,
            external_space=external.space,
        )

    @staticmethod
    def create_replay_pair(
        *,
        is_passthrough: PassthroughPredicate,
        next_result: NextResult,
        bind: BindCallback,
        proxy_type_customizer: Callable[..., Any] = utils.noop,
        internal_space=None,
        external_space=None,
    ) -> GatewayPair:
        """Create replay-time gateways.

        ``is_passthrough`` is the same proxy-skipping predicate accepted by
        ``create_recording_pair``.
        """
        internal = create_endpoint(internal_space)
        external = create_endpoint(external_space)

        _space_wrap(internal.space, create_ext_proxytype_from_spec(
            int_gateway=internal.gateway,
            ext_gateway=external.gateway,
            bind=bind,
            customize_proxy_type=proxy_type_customizer,
        ))

        def illegal_external_proxy(value):
            raise RuntimeError("replay cannot create external proxies from live values")

        external = external._replace(proxy=illegal_external_proxy)

        proxytype_factory = ProxytypeFactory(
            internal=internal,
            external=external,
            bind=bind,
            is_patched_type=utils.FastTypePredicate(lambda cls: False).istypeof,
            proxy_ref=functional.memoize_one_arg(ProxyRef),
            customize_proxy_type=proxy_type_customizer,
        )
        internal_proxy = int_proxy_factory(
            proxytype=retrace.root_space.wrap(
                functional.compose(
                    proxytype_factory.int_proxytype,
                    functional.side_effect(bind))),
            bind=bind)
        internal = internal._replace(
            proxy=functional.if_then_else(
                is_passthrough,
                functional.identity,
                internal_proxy,
            ),
        )

        _wire_for_replay(
            internal=internal,
            external=external,
            next_result=next_result,
        )
        return GatewayPair(
            internal=_space_wrap(external.space, internal.gateway),
            external=_space_wrap(internal.space, external.gateway),
            sandbox_space=internal.space,
            external_space=external.space,
        )

    create_replaying_pair = create_replay_pair
