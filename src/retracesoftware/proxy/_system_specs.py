"""Context and spec builders for the gate-based proxy system."""

from types import SimpleNamespace

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.proxytype import dynamic_int_proxytype, dynamic_proxytype

from ._system_patching import Patched


def create_context(
    system,
    int_spec,
    ext_spec,
    ext_runner=None,
    on_start=None,
    on_end=None,
    **args,
):
    """Build the executor pair and enter a gate context."""
    from .context import Context

    return Context.from_specs(
        system,
        int_spec=int_spec,
        ext_spec=ext_spec,
        ext_runner=ext_runner,
        on_start=on_start,
        on_end=on_end,
        **{
            {
                "_bind": "bind",
                "_async_new_patched": "async_new_patched",
                "on_bind": "on_bind",
            }.get(key, key): value
            for key, value in args.items()
        },
    )


def create_int_spec(system, bind, checkpoint = None, on_call=None, on_result=None, on_error=None):
    """Build the internal (ext→int) specification."""

    def int_proxytype(cls):
        return dynamic_int_proxytype(
            handler=system._internal,
            cls=cls,
            bind = bind,
            checkpoint = checkpoint,
        )

    # int_spec.proxy is used while preparing an outbound int->ext call.
    # If a patched value reaches this path and wasn't already handled by
    # int_passthrough, it means we have a mixed live/retraced call shape,
    # so raise the passthrough sentinel and let System._ext_handler run the
    # original target locally instead of partially routing through retrace.
            
    maybe_int_proxy = functional.if_then_else(
        system.int_passthrough,
        functional.identity,
        functional.if_then_else(
            system.is_patched,
            system._throw_passthrough,
            system._proxyfactory(
                system.disable_for(int_proxytype), 
                on_instance = functional.side_effect(bind))
        ),
    )

    return SimpleNamespace(
        proxy=maybe_int_proxy,
        on_call=on_call,
        on_result=on_result,
        on_error=on_error,
    )


def create_ext_spec(
    system,
    on_call,
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
            function=system.execute,
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

    # ext_spec.proxy handles values flowing back from the external side
    # (external results and args entering ext->int callbacks). Patched
    # values are valid here: on record we track/bind fresh patched objects
    # that cross back in, while ordinary external values are dynamically
    # proxied. This is intentionally not the mixed-state bailout path.
    maybe_ext_proxy = functional.if_then_else(
        system.ext_passthrough,
        functional.identity,
        functional.if_then_else(
            is_patched_type,
            track if track else functional.identity,
            system._proxyfactory(system.disable_for(ext_proxytype)),
        ),
    )

    return SimpleNamespace(
        proxy=maybe_ext_proxy,
        on_call=on_call,
        on_result=on_result,
        on_passthrough_result=on_passthrough_result,
        on_error=on_error,
    )
