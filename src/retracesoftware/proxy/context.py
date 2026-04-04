"""High-level runnable proxy context."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from typing import Any, Callable, NamedTuple

from ._system_adapters import _run_with_replay, adapter
from ._system_context import _GateContext, Handler
from ._system_specs import create_ext_spec, create_int_spec


class CallHooks(NamedTuple):
    """Lifecycle callbacks for one side of the proxy boundary."""

    on_call: Callable[..., Any] | None = None
    on_result: Callable[..., Any] | None = None
    on_error: Callable[..., Any] | None = None


class LifecycleHooks(NamedTuple):
    on_start: Callable[..., Any] | None = None
    on_end: Callable[..., Any] | None = None


class Context:
    """Frozen runnable proxy context."""

    __slots__ = ("_context",)

    def __init__(
        self,
        system,
        *,
        internal_hooks: CallHooks,
        external_hooks: CallHooks,
        lifecycle_hooks: LifecycleHooks,
        bind = None,
        checkpoint = None,
        track = None,
        async_new_patched = None,
        ext_runner = None,
        on_new_proxytype = None,
        on_bind = None,
    ):
        
        # ext_proxy = functional.if_then_else(
        #     system.ext_passthrough,
        #     functional.identity,
        #     functional.walker(

        #     functional.compose(..., system.bind_unbound)
        #     functional.if_then_else(
        #         is_patched_type,
        #         track if track else functional.identity,
        #         system._proxyfactory(system.disable_for(ext_proxytype)),
        #     ),
        # )

        run_external = functional.partial(
            self.system._external.apply_with(None),
            self.system.execute,
        )

        self._external = adapter(
            function = run_external,
            passthrough = system.passthrough,
            proxy_input = system.int_proxy,
            unproxy_input = system.unproxy_ext,
            proxy_output = system.ext_proxy,
            unproxy_output = system.unproxy_int,
            on_call = external_hooks.on_call,
            on_result = external_hooks.on_result,
            on_error = external_hooks.on_error,
        )

        run_internal = functional.partial(
            self.system._external.apply_with(self._external),
            self.system.execute,
        )

        self._internal = adapter(
            function=run_internal,
            passthrough = system.passthrough,
            proxy_input = system.ext_proxy,
            unproxy_input = system.unproxy_int,
            proxy_output = system.int_proxy,
            unproxy_output = system.unproxy_ext,
            on_call = internal_hooks.on_call,
            on_result = internal_hooks.on_result,
            on_error = internal_hooks.on_error,
        )

        disabled_handler = None
        internal_handler = None

        if ext_runner is not None:
            live_passthrough = functional.mapargs(
                starting=1,
                transform=utils.try_unwrap,
                function=system.execute,
            )
            disabled_handler = live_passthrough
            internal_handler = live_passthrough

        if track is None:
            track = system.bind_unbound

        int_spec = create_int_spec(
            system,
            bind=system.bind,
            checkpoint=checkpoint,
            on_call=internal_hooks.on_call,
            on_result=internal_hooks.on_result,
            on_error=internal_hooks.on_error,
        )
        ext_spec = create_ext_spec(
            system,
            on_call=external_hooks.on_call,
            on_result=external_hooks.on_result,
            on_error=external_hooks.on_error,
            track=track,
            on_new_proxytype=on_new_proxytype,
            disabled_handler=disabled_handler,
            internal_handler=internal_handler,
        )

        self._context = self.from_specs(
            system,
            int_spec=int_spec,
            ext_spec=ext_spec,
            bind=bind,
            async_new_patched=async_new_patched,
            ext_runner=ext_runner,
            on_bind=on_bind,
            on_start=lifecycle_hooks.on_start,
            on_end=lifecycle_hooks.on_end,
        )._context

    @classmethod
    def from_specs(
        cls,
        system,
        *,
        int_spec,
        ext_spec,
        bind=None,
        async_new_patched=None,
        ext_runner=None,
        on_bind=None,
        on_start=None,
        on_end=None,
    ):
        self = cls.__new__(cls)

        if on_bind is not None:
            bind = on_bind if bind is None else utils.runall(on_bind, bind)

        function = _run_with_replay(ext_runner) if ext_runner else functional.partial(
            system._external.apply_with(None),
            system.execute,
        )

        unproxy_int = functional.if_then_else(
            functional.isinstanceof(utils.InternalWrapped),
            utils.unwrap,
            functional.identity,
        )

        unproxy_ext = functional.if_then_else(
            functional.isinstanceof(utils.ExternalWrapped),
            utils.unwrap,
            functional.identity,
        )

        ext_executor = adapter(
            function=function,
            passthrough=system.passthrough,
            proxy_input=int_spec.proxy,
            unproxy_input=unproxy_ext,
            proxy_output=ext_spec.proxy,
            unproxy_output=unproxy_int,
            on_call=ext_spec.on_call,
            on_result=ext_spec.on_result,
            on_passthrough_result=getattr(ext_spec, "on_passthrough_result", None),
            on_error=ext_spec.on_error,
        )

        int_executor = adapter(
            function=functional.partial(
                system._external.apply_with(ext_executor),
                system.execute,
            ),
            passthrough=system.passthrough,
            proxy_input=ext_spec.proxy,
            unproxy_input=unproxy_int,
            proxy_output=int_spec.proxy,
            unproxy_output=unproxy_ext,
            on_call=int_spec.on_call,
            on_result=int_spec.on_result,
            on_error=int_spec.on_error,
        )

        self._context = _GateContext(
            system,
            internal=Handler(int_executor),
            external=Handler(ext_executor),
            bind=bind,
            async_new_patched=async_new_patched,
            on_start=on_start,
            on_end=on_end,
        )
        return self

    def __enter__(self):
        return self._context.__enter__()

    def __exit__(self, *exc):
        return self._context.__exit__(*exc)
