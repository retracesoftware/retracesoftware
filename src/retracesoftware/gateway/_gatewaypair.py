from __future__ import annotations

from typing import Any, Callable, NamedTuple, Protocol
import threading

import retrace

from retracesoftware import utils
from retracesoftware import functional
import retracesoftware.gateway._dynamicproxy as dynamicproxy
from retracesoftware.gateway._dynamicproxy import int_proxy_factory


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
    __slots__ = (
        "internal",
        "external",
        "_sandbox_space",
        "_external_space",
        "_internal_endpoint",
        "_external_endpoint",
        "_bind",
    )

    def __init__(
        self,
        *,
        internal: Callable[..., Any] | None = None,
        external: Callable[..., Any] | None = None,
        sandbox_space: Any = None,
        external_space: Any = None,
        internal_endpoint: Endpoint | None = None,
        external_endpoint: Endpoint | None = None,
        bind: BindCallback = utils.noop,
    ) -> None:
        if (
            internal is None
            and external is None
            and sandbox_space is None
            and external_space is None
            and internal_endpoint is None
            and external_endpoint is None
        ):
            internal_endpoint = create_endpoint()
            external_endpoint = create_endpoint()
            sandbox_space = internal_endpoint.space
            external_space = external_endpoint.space
            internal = _space_wrap(external_space, internal_endpoint.gateway)
            external = _space_wrap(sandbox_space, external_endpoint.gateway)

        if internal is None or external is None:
            raise TypeError("GatewayPair requires both internal and external callables")
        if sandbox_space is None or external_space is None:
            raise TypeError("GatewayPair requires both sandbox_space and external_space")

        self.internal = internal
        self.external = external
        self._sandbox_space = sandbox_space
        self._external_space = external_space
        self._internal_endpoint = internal_endpoint
        self._external_endpoint = external_endpoint
        self._bind = bind

    @property
    def sandbox_space(self) -> Any:
        return self._sandbox_space

    @staticmethod
    def create_unwired(
        *,
        internal_space=None,
        external_space=None,
        bind: BindCallback = utils.noop,
    ) -> GatewayPair:
        internal_endpoint = create_endpoint(internal_space)
        external_endpoint = create_endpoint(external_space)

        return GatewayPair(
            internal=_space_wrap(external_endpoint.space, internal_endpoint.gateway),
            external=_space_wrap(internal_endpoint.space, external_endpoint.gateway),
            sandbox_space=internal_endpoint.space,
            external_space=external_endpoint.space,
            internal_endpoint=internal_endpoint,
            external_endpoint=external_endpoint,
            bind=bind,
        )

    def _require_unwired_endpoints(self) -> tuple[Endpoint, Endpoint]:
        if self._internal_endpoint is None or self._external_endpoint is None:
            raise RuntimeError("GatewayPair was not created by create_unwired")
        return self._internal_endpoint, self._external_endpoint

    def wrap_as_callback(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """Return ``function`` wrapped so external calls re-enter internally.

        Generated proxy helpers sometimes need to hand a Python callable to the
        external side.  If external code calls that helper, the call is a normal
        external-to-internal callback and must go through the internal gateway
        so record/replay observes the callback envelope.  The wrapper itself is
        bound because the callable crossing the boundary is the wrapper, not the
        original function.
        """
        wrapped = utils.wrapped_function(handler=self.internal, target=function)
        self._bind(wrapped)
        return wrapped

    def _dynamic_external_type_from_internal(self, proxy_type_factory):
        from_spec = _space_wrap(
            self._sandbox_space,
            proxy_type_factory.dynamic_external_type_from_spec,
        )

        def dynamic_external_type(cls):
            return proxy_type_factory.dynamic_external_type(
                cls,
                from_spec=from_spec,
            )

        return dynamic_external_type

    def wire_recording(
        self,
        proxy_type_factory,
        *,
        is_passthrough: PassthroughPredicate,
        on_callback: CallbackObserver,
        on_error: ErrorCallback,
        on_result: ResultCallback,
    ) -> GatewayPair:
        """Install record-time dispatch and proxy hooks on an unwired pair."""
        internal, external = self._require_unwired_endpoints()

        external = external._replace(
            proxy=dynamicproxy.proxy(
                retrace.root_space.wrap(
                    self._dynamic_external_type_from_internal(proxy_type_factory)
                )
            ),
        )

        internal_proxy = int_proxy_factory(
            proxytype=retrace.root_space.wrap(
                proxy_type_factory.dynamic_internal_type),
            bind=utils.noop)
        internal = internal._replace(
            proxy=functional.if_then_else(
                is_passthrough,
                functional.identity,
                internal_proxy,
            ),
        )

        self._internal_endpoint = internal
        self._external_endpoint = external
        return self.wire_for_record(
            on_callback=on_callback,
            on_error=on_error,
            on_result=on_result,
            is_passthrough=is_passthrough,
            int_proxy=internal.proxy,
            ext_proxy=external.proxy,
        )

    def wire_replay(
        self,
        proxy_type_factory,
        *,
        is_passthrough: PassthroughPredicate,
        next_result: NextResult,
    ) -> GatewayPair:
        """Install replay-time dispatch and proxy hooks on an unwired pair."""
        internal, external = self._require_unwired_endpoints()
        self._dynamic_external_type_from_internal(proxy_type_factory)

        def illegal_external_proxy(value):
            raise RuntimeError("replay cannot create external proxies from live values")

        external = external._replace(proxy=illegal_external_proxy)

        internal_proxy = int_proxy_factory(
            proxytype=retrace.root_space.wrap(
                proxy_type_factory.dynamic_internal_type),
            bind=utils.noop)
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

        self._internal_endpoint = internal
        self._external_endpoint = external
        return self

    def set_handlers(self, *, internal, external):
        internal_endpoint, external_endpoint = self._require_unwired_endpoints()
        internal_endpoint.gateway[self._external_space] = internal
        external_endpoint.gateway[self._sandbox_space] = external

    def wire_for_record(
        self,
        *,
        is_passthrough: PassthroughPredicate,
        on_callback: CallbackObserver,
        on_error: ErrorCallback,
        on_result: ResultCallback,
        int_proxy: Callable[..., Any],
        ext_proxy: Callable[..., Any],
    ):
        """Install record-time dispatch and proxy hooks on an unwired pair."""
        unwrap = functional.walker(utils.try_unwrap)
        external_arg = functional.if_then_else(
            utils.is_wrapped,
            utils.try_unwrap,
            int_proxy,
        )

        callback_arg = functional.walker(
            functional.if_then_else(
                is_passthrough,
                functional.identity,
                ext_proxy,
            )
        )

        external_result = functional.if_then_else(
            is_passthrough,
            functional.side_effect(on_result),
            functional.sequence(
                ext_proxy,
                functional.side_effect(on_result),
                unwrap,
            ),
        )

        callback_call = functional.mapargs(
            _space_apply_callable(self._sandbox_space),
            unwrap,
        )
        callback_runner = utils.observer(
            callback_call,
            on_call=on_callback,
        )

        self.set_handlers(
            external=functional.transform_call(
                _space_apply_callable(self._external_space),
                utils.try_unwrap,
                rest_transform=external_arg,
                result_transform=external_result,
                on_error=on_error,
            ),
            internal=functional.transform_call(
                callback_runner,
                utils.try_unwrap,
                rest_transform=callback_arg,
                result_transform=int_proxy,
            ),
        )

        return self

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
        gateway_pair: GatewayPair | None = None,
        proxy_type_factory=None,
    ) -> GatewayPair:
        """Create record-time gateways.

        ``is_passthrough`` is a predicate called with values crossing back to
        external code.  It returns ``True`` when that value is already safe to
        pass through unchanged, and ``False`` when GatewayPair must proxy it.
        """
        pair = gateway_pair or GatewayPair.create_unwired(
            internal_space=internal_space,
            external_space=external_space,
            bind=bind,
        )
        pair._bind = bind
        if proxy_type_factory is None:
            from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory

            proxy_type_factory = ProxyTypeFactory(
                gateway_pair=pair,
                proxy_type_customizer=proxy_type_customizer,
            )

        return pair.wire_recording(
            proxy_type_factory,
            is_passthrough=is_passthrough,
            on_callback=on_callback,
            on_error=on_error,
            on_result=on_result,
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
        gateway_pair: GatewayPair | None = None,
        proxy_type_factory=None,
    ) -> GatewayPair:
        """Create replay-time gateways.

        ``is_passthrough`` is the same proxy-skipping predicate accepted by
        ``create_recording_pair``.
        """
        pair = gateway_pair or GatewayPair.create_unwired(
            internal_space=internal_space,
            external_space=external_space,
            bind=bind,
        )
        pair._bind = bind
        if proxy_type_factory is None:
            from retracesoftware.proxy.proxytypefactory2 import ProxyTypeFactory

            proxy_type_factory = ProxyTypeFactory(
                gateway_pair=pair,
                proxy_type_customizer=proxy_type_customizer,
            )

        return pair.wire_replay(
            proxy_type_factory,
            is_passthrough=is_passthrough,
            next_result=next_result,
        )

    create_replaying_pair = create_replay_pair
