from retracesoftware import utils, functional

def when_instanceof(cls, on_then, on_else = functional.identity):
    return functional.if_then_else(functional.isinstanceof(cls), on_then, on_else)

unproxy_ext = functional.walker(when_instanceof(utils.ExternalWrapped, utils.unwrap))

unproxy_int = functional.walker(when_instanceof(utils.InternalWrapped, utils.unwrap))

def int_runner(gate, int_proxy):
    return gate.apply_with('internal', functional.mapargs(
        starting = 1,
        transform = unproxy_int,
        function = functional.sequence(utils.try_unwrap_apply, int_proxy)))

def ext_runner(gate, ext_proxy):
    return gate.apply_with('external', functional.mapargs(
        starting = 1,
        transform = unproxy_ext,
        function = functional.sequence(utils.try_unwrap_apply, ext_proxy)))

def ext_gateway(gate, int_proxy, ext_proxy, hooks):
    runner = ext_runner(gate, ext_proxy)

    observer = utils.observer(
        on_call = hooks.on_call,
        on_result = hooks.on_result,
        on_error = hooks.on_error,
        function = runner)

    return functional.sequence(
            functional.mapargs(
                starting = 1,
                transform = int_proxy,
                function = observer),
            unproxy_int)

def ext_replay_gateway(ext_runner, gate, int_proxy, ext_proxy, hooks):
    
    observer = utils.observer(
        on_call = hooks.on_call,
        on_result = hooks.on_result,
        on_error = hooks.on_error,
        function = gate.apply_with('external', ext_runner))

    return functional.sequence(
            functional.mapargs(
                starting = 1,
                transform = int_proxy,
                function = observer),
            unproxy_int)

def int_replay_gateway(gate, int_proxy, ext_proxy, hooks):
    return utils.observer(
        on_call = hooks.on_call,
        on_result = hooks.on_result,
        on_error = hooks.on_error,
        function = int_runner(gate, int_proxy))

def int_gateway(gate, int_proxy, ext_proxy, hooks):
    runner = int_runner(gate, int_proxy)

    observer = utils.observer(
        on_call = hooks.on_call,
        on_result = hooks.on_result,
        on_error = hooks.on_error,
        function = runner)

    return functional.sequence(
            functional.mapargs(
                starting = 1,
                transform = ext_proxy,
                function = observer),
            unproxy_ext)
