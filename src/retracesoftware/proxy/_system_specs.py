"""Context and spec builders for the gate-based proxy system."""

from types import SimpleNamespace

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.proxytype import dynamic_int_proxytype, dynamic_proxytype

from ._system_adapters import _run_with_replay, adapter
from ._system_patching import Patched


def create_context(
    system,
    int_spec,
    ext_spec,
    ext_runner=None,
    replay_bind_materialized=None,
    adapter_fn=adapter,
    replay_runner_fn=_run_with_replay,
    **args,
):
    """Build the executor pair and enter a gate context."""

    function = replay_runner_fn(
        ext_runner,
        replay_materialize=system.replay_materialize,
        materialize=lambda fn, *fn_args, **fn_kwargs: system.disable_for(fn)(*fn_args, **fn_kwargs),
        bind_materialized=replay_bind_materialized
        or functional.when(
            lambda value: system.should_proxy(value) and not system.is_bound(value),
            lambda value: (system._bind(value), system.is_bound.add(value), value)[-1],
        ),
    ) if ext_runner else system._external.apply_with(None)

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

    passthrough = functional.or_predicate(
        utils.FastTypePredicate(
            lambda cls: issubclass(cls, tuple(system.immutable_types))
        ).istypeof,
        system.is_bound,
    )

    ext_executor = adapter_fn(
        function=function,
        passthrough=passthrough,
        proxy_input=int_spec.proxy,
        unproxy_input=unproxy_ext,
        proxy_output=ext_spec.proxy,
        unproxy_output=unproxy_int,
        on_call=ext_spec.on_call,
        on_result=ext_spec.on_result,
        on_passthrough_result=getattr(ext_spec, "on_passthrough_result", None),
        on_error=ext_spec.on_error,
    )

    int_executor = adapter_fn(
        function=system._external.apply_with(ext_executor),
        passthrough=passthrough,
        proxy_input=ext_spec.proxy,
        unproxy_input=unproxy_int,
        proxy_output=int_spec.proxy,
        unproxy_output=unproxy_ext,
        on_call=int_spec.on_call,
        on_result=int_spec.on_result,
        on_error=int_spec.on_error,
    )

    return system._context(
        _internal=int_executor,
        _external=ext_executor,
        **args,
    )


def create_int_spec(system, bind, on_call=None, on_result=None, on_error=None):
    """Build the internal (ext→int) specification."""

    def int_proxytype(cls):
        return dynamic_int_proxytype(
            handler=system._internal,
            cls=cls,
            bind=bind,
        )

    return SimpleNamespace(
        proxy=system._proxyfactory(system.disable_for(int_proxytype)),
        on_call=on_call,
        on_result=on_result,
        on_error=on_error,
    )


def create_ext_spec(
    system,
    sync,
    on_result,
    on_error,
    track,
    on_passthrough_result=None,
    on_new_proxytype=None,
    disabled_handler=None,
    internal_handler=None,
):
    """Build the external (int→ext) specification."""

    if disabled_handler is None:
        disabled_handler = functional.mapargs(
            starting=1,
            transform=utils.try_unwrap,
            function=functional.apply,
        )

    if internal_handler is None:
        internal_handler = system._external

    handler = system.create_gate(
        disabled=disabled_handler,
        external=system._external,
        internal=internal_handler,
    )

    def ext_proxytype(cls):
        proxytype = dynamic_proxytype(handler=handler, cls=cls)
        proxytype.__retrace_source__ = "external"

        if issubclass(cls, Patched):
            patched = cls
        elif cls in system.base_to_patched:
            patched = system.base_to_patched[cls]
        else:
            patched = None

        assert patched is None or patched.__base__ is not object

        if patched:
            patcher = getattr(patched, "__retrace_patch_proxy__", None)
            if patcher:
                patcher(proxytype)

        if on_new_proxytype:
            on_new_proxytype(proxytype, cls)

        return proxytype

    is_patched_type = utils.FastTypePredicate(
        lambda cls: cls in system.patched_types
    ).istypeof

    if track:
        proxy = functional.if_then_else(
            is_patched_type,
            track,
            system._proxyfactory(system.disable_for(ext_proxytype)),
        )
    else:
        proxy = system._proxyfactory(system.disable_for(ext_proxytype))

    return SimpleNamespace(
        proxy=proxy,
        on_call=functional.lazy(sync),
        on_result=on_result,
        on_passthrough_result=on_passthrough_result,
        on_error=on_error,
    )
