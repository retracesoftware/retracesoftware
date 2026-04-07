"""IO bridge helpers for the gate-based ``System``."""
from contextlib import contextmanager
import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System
from retracesoftware.proxy.tape import TapeReader, TapeWriter
import gc
import sys
import os


class ReplayException(Exception):
    pass


def equal(a, b):
    if a is b:
        return True

    if type(a) is not type(b):
        return False
    
    cls = type(a)

    if issubclass(cls, utils.ExternalWrapped):
        return True

    if cls is tuple or cls is list:
        if len(a) != len(b):
            return False            
        return all(equal(a[i], b[i]) for i in range(len(a)))

    if cls is dict:
        if len(a) != len(b):
            return False
        if a.keys() != b.keys():
            return False
        return all(equal(a[k], b[k]) for k in a.keys())

    return a == b

def _safe_debug_value(value, depth=0):
    if depth > 2:
        return f"<{type(value).__name__}>"

    if isinstance(value, (str, int, float, bool, type(None), bytes)):
        return repr(value)

    if isinstance(value, tuple):
        inner = ", ".join(_safe_debug_value(v, depth + 1) for v in value[:4])
        if len(value) > 4:
            inner += ", ..."
        return f"({inner})"

    if isinstance(value, list):
        inner = ", ".join(_safe_debug_value(v, depth + 1) for v in value[:4])
        if len(value) > 4:
            inner += ", ..."
        return f"[{inner}]"

    if isinstance(value, dict):
        items = list(value.items())[:4]
        inner = ", ".join(
            f"{_safe_debug_value(k, depth + 1)}: {_safe_debug_value(v, depth + 1)}"
            for k, v in items
        )
        if len(value) > 4:
            inner += ", ..."
        return "{" + inner + "}"

    try:
        return object.__repr__(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _on_call(fn, *args, **kwargs):
    return {
        "function": fn,
        "args": args,
        "kwargs": kwargs,
    }

def _on_result(result): return {"result": result}

def _on_error(error): return {"error": error}

def _secondary_hooks(sync, checkpoint):
    if checkpoint:
        return CallHooks(
            on_call=functional.sequence(_on_call, checkpoint), 
            on_result=functional.sequence(_on_result, checkpoint),
            on_error=functional.sequence(functional.positional_param(1), _on_error, checkpoint))
    else:
        return CallHooks(
            on_call=sync,
            on_result=sync,
            on_error=sync,
        )

def normalize_stack_delta(delta):
    to_drop, frames = delta
    return (
        to_drop,
        tuple(
            (frame.filename, frame.lineno)
            if isinstance(frame, utils.Stack)
            else tuple(frame)
            for frame in frames
        ),
    )

def recorder(*, tape_writer: TapeWriter, 
    debug: bool = False,
    stacktraces: bool = False,
    gc_collect_multiplier: int = None) -> System:
    write = tape_writer.write
    bind = tape_writer.bind

    
    checkpoint = functional.partial(write, "CHECKPOINT")
    stacktrace = functional.partial(write, "STACKTRACE")
    sync = functional.repeatedly(write, "SYNC")

    stack = utils.StackFactory()

    def on_start():
        stack.delta() # reset the stack position for delta
        write("ON_START")

    on_bind = bind

    in_sandbox = functional.constantly(False)

    secondary_hooks = _secondary_hooks(
        sync = sync,
        checkpoint = checkpoint if debug else None)

    if stacktraces:
        write_stacktrace = functional.repeatedly(functional.if_then_else(
            lambda: in_sandbox(),        
            functional.sequence(stack.delta, normalize_stack_delta, stacktrace),
            utils.noop))

        def with_stacktrace(function):
            return utils.observer(on_call = write_stacktrace, function = function)

        on_bind = with_stacktrace(on_bind)
        secondary_hooks = CallHooks(
            on_call = with_stacktrace(secondary_hooks.on_call),
            on_result = secondary_hooks.on_result,
            on_error = secondary_hooks.on_error,
        )

    system = System(on_bind)
    in_sandbox = system._in_sandbox

    create_stub_object = system.wrap_async(utils.create_stub_object)
    collect = system.wrap_async(gc.collect)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system._in_sandbox),
        checkpoint, utils.noop)

    system.lifecycle_hooks = LifecycleHooks(
            on_start = on_start,
            on_end = None,
            #functional.repeatedly(write, "ON_END")
            )
            
    on_call = functional.pack_call(1, functional.partial(write, "CALL"))
    on_result = functional.partial(write, "RESULT")
    on_error = functional.sequence(
        functional.positional_param(1), 
        functional.if_then_else(
            functional.isinstanceof(Exception),
            functional.partial(write, "ERROR"),
            utils.noop))

    if gc_collect_multiplier:
        gc_collector = utils.Collector(multiplier = gc_collect_multiplier, collect = collect)
        on_result = utils.observer(on_call = gc_collector, function = on_result)
        
    system.primary_hooks = CallHooks(
            on_call = on_call,
            on_result = on_result,
            on_error = on_error)

    system.secondary_hooks = secondary_hooks

    @utils.exclude_from_stacktrace
    def async_new_patched(obj):
        system.primary_hooks.on_call(create_stub_object, type(obj))
        system.bind(obj)
        system.secondary_hooks.on_result(obj)

    system.async_new_patched = async_new_patched

    system.passthrough_proxyref = True

    return system

@contextmanager
def recorder_context(**kwargs):
    system = recorder(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()

def next_result(*, on_stacktrace, run_callback, on_unexpected, read):
    actions = {
        # "STACKTRACE": utils.observer(on_call = on_stacktrace, function = utils.noop),
        "CALL": utils.observer(on_call = run_callback, function = utils.noop),
        "RESULT": read, 
        "ERROR": functional.spread(utils.throw, read)
    }

    next = functional.spread(
        functional.apply,
        functional.spread(
            functional.if_then_else(
                actions.__contains__, 
                actions.get,
                on_unexpected),
            read))
    
    # actions["STACKTRACE"].function = next
    actions["CALL"].function = next

    return next

def default_unexpected_handler(key):
    print(f"Unexpected message: {key}, was expecting a result, error, or call", file=sys.stderr)
    os._exit(1)

def default_desync_handler(record, replay):
    print(f"Checkpoint difference: {_safe_debug_value(record)} was expecting {_safe_debug_value(replay)}", file=sys.stderr)
    os._exit(1)

def replayer(*, tape_reader: TapeReader, 
             on_unexpected = default_unexpected_handler,
             on_desync = default_desync_handler,
             debug: bool = False,
             stacktraces: bool = False) -> System:
    read = tape_reader.read
    bind = tape_reader.bind

    stack = utils.StackFactory()
    current_stack = utils.ThreadLocal([])

    def trim_replay_stack(replay, recorded):
        if len(replay) >= len(recorded):
            for start in range(len(replay) - len(recorded) + 1):
                candidate = replay[start:start + len(recorded)]
                if candidate == recorded:
                    return candidate
        return replay

    def on_stacktrace():
        to_drop, new_frames = read()
        this_stack = current_stack.get()
        del this_stack[:to_drop]
        this_stack[:0] = [
            (frame.filename, frame.lineno)
            if isinstance(frame, utils.Stack)
            else tuple(frame)
            for frame in new_frames
        ]

        replay = trim_replay_stack(list(stack())[2:], this_stack)
        if replay[1:] != this_stack[1:]:
            on_desync(replay, this_stack)
        
    @utils.exclude_from_stacktrace
    def expect(value):
        type = read()

        if type != value:
            if type == "STACKTRACE":
                on_stacktrace()
                expect(value)
            elif type == "CALL":
                run_callback()
                expect(value)
            else:                    
                on_desync(type, value)

    # sync = functional.repeatedly(expect, "SYNC")

    run_callback = functional.catch_exception(functional.spread(functional.call, read, read, read), Exception, utils.noop)
            
    # safeequal = system.disable_for(equal)
    def diff(record, replay):
        if not equal(record, replay):
            on_desync(record, replay)

    def checkpoint(replay):
        expect('CHECKPOINT')
        diff(record = read(), replay = replay)

    in_sandbox = functional.constantly(False)

    sync = functional.repeatedly(expect, "SYNC")

    secondary_hooks = _secondary_hooks(
        sync = sync,
        checkpoint = checkpoint if debug else None)

    if stacktraces:
        def next_stacktrace(*args, **kwargs):
            if in_sandbox():
                type = read()
                if type == "STACKTRACE":
                    on_stacktrace()
                else:
                    raise ReplayException(f"Unexpected message: {type}, was expecting a stacktrace")
                
        bind = functional.sequence(functional.side_effect(next_stacktrace), bind)
        secondary_hooks = CallHooks(
            on_call = functional.sequence(functional.side_effect(next_stacktrace), secondary_hooks.on_call),
            on_result = secondary_hooks.on_result,
            on_error = secondary_hooks.on_error,
        )

    system = System(bind)
    in_sandbox = system._in_sandbox

    system.wrap_async(utils.create_stub_object)
    system.wrap_async(gc.collect)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system._in_sandbox),
        checkpoint, utils.noop)

    system.primary_hooks = None

    system.secondary_hooks = secondary_hooks

    def on_start():
        stack.delta() # reset the stack position for delta
        expect("ON_START")

    system.lifecycle_hooks=LifecycleHooks(
        on_start=on_start,
        on_end=None
        # on_end=functional.repeatedly(expect, "ON_END"),
        )

    system.ext_execute = functional.repeatedly(
        next_result(
            on_stacktrace = on_stacktrace, 
            run_callback = run_callback, 
            on_unexpected = system.disable_for(on_unexpected),
            read = read))

    return system


@contextmanager
def replayer_context(**kwargs):
    system = replayer(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()
