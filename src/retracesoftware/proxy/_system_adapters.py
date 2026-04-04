"""Pure adapter helpers for the gate-based proxy system."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils


def _run_with_replay(ext_runner):
    """Return a replay callable matching apply_with's signature.

    During replay, the real external function is never called. Instead,
    ``ext_runner()`` reads the next recorded result from the stream and
    returns it directly. ``fn``, ``args``, and ``kwargs`` are ignored.
    """

    def replay_fn(fn, *args, **kwargs):
        return ext_runner()

    return replay_fn


def input_adapter(function, proxy, unproxy, on_call=None):
    function = functional.mapargs(
        transform=functional.walker(unproxy),
        function=function,
    )
    
    function = utils.observer(on_call=on_call, function=function)

    function = functional.mapargs(
        starting=1,
        transform=functional.walker(proxy),
        function=function,
    )
    # else:
    #     # slow_path = functional.walker(functional.sequence(proxy, unproxy))
    #     # transform = functional.when_not(passthrough, slow_path)
    #     # function = functional.mapargs(starting=1, transform=transform, function=function)

    return function


def output_transform(
    passthrough,
    proxy,
    unproxy,
    on_result=None,
    on_passthrough_result=None,
):
    if on_result:
        slow_path = functional.sequence(
            functional.walker(proxy),
            functional.side_effect(on_result),
            functional.walker(unproxy),
        )

        passthrough_result = on_passthrough_result or on_result
        return functional.if_then_else(passthrough, functional.side_effect(passthrough_result), slow_path)
    else:
        transform = functional.sequence(proxy, unproxy)
        slow_path = functional.walker(transform)
        if on_passthrough_result:
            return functional.if_then_else(
                passthrough, functional.side_effect(on_passthrough_result), slow_path
            )
        return functional.when_not(passthrough, slow_path)


def adapter(
    function,
    passthrough,
    proxy_input,
    proxy_output,
    unproxy_input=functional.identity,
    unproxy_output=functional.identity,
    on_call=None,
    on_result=None,
    on_passthrough_result=None,
    on_error=None,
):
    if on_error:
        function = utils.observer(on_error=on_error, function=function)

    output_transformer = output_transform(
        passthrough, proxy_output, unproxy_output, on_result, on_passthrough_result
    )

    function = functional.sequence(function, output_transformer)

    return input_adapter(function, proxy_input, unproxy_input, on_call)


def proxy(proxytype_from):
    """Create a callable that wraps a value in a proxy type."""

    # cls = proxytype_from(type(value))
    # wrapped = utils.create_wrapped(cls, value)
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, proxytype_from),
        functional.identity,
    )


def maybe_proxy(proxytype_from, on_instance = None):
    """Conditionally proxy a value."""

    return functional.if_then_else(
        functional.isinstanceof(utils.Wrapped),
        functional.identity,
        functional.sequence(proxy(functional.memoize_one_arg(proxytype_from)), on_instance),
    )
