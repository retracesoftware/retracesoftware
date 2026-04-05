"""IO bridge helpers for the gate-based ``System``."""
from contextlib import contextmanager
import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System
from retracesoftware.proxy.tape import TapeReader, TapeWriter


class ReplayException(Exception):
    pass


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

def recorder(*, tape_writer: TapeWriter, debug: bool = False, stacktraces: bool = False) -> System:
    write = tape_writer.write
    bind = tape_writer.bind

    checkpoint = functional.partial(write, "CHECKPOINT")

    sync = functional.repeatedly(write, "SYNC")

    stack = utils.StackFactory()

    def on_start():
        stack() # reset the stack position for delta
        write("ON_START")

    on_bind = bind

    if stacktraces:

        write_stacktrace = \
            functional.sequence(
                functional.repeatedly(stack.delta),
                functional.partial(write, "STACKTRACE"))

        def with_stacktrace(function):
            return utils.observer(on_call = write_stacktrace, function = function)

        on_bind = with_stacktrace(on_bind)
        sync = with_stacktrace(sync)
        checkpoint = with_stacktrace(checkpoint)

    on_call = functional.pack_call(1, functional.partial(write, "CALL"))
    on_result = functional.partial(write, "RESULT")
    on_error = functional.sequence(functional.positional_param(1), functional.partial(write, "ERROR"))

    return System(
        primary_hooks=CallHooks(
            on_call = on_call,
            on_result = on_result,
            on_error = on_error,
        ),
        secondary_hooks = _secondary_hooks(
            sync = sync,
            checkpoint = checkpoint if debug else None,
        ),
        lifecycle_hooks=LifecycleHooks(
            on_start=on_start,
            on_end=functional.repeatedly(write, "ON_END")),
        on_bind=on_bind,
        passthrough_proxyref=True,
    )

@contextmanager
def recorder_context(**kwargs):
    system = recorder(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()

def replayer(*, tape_reader: TapeReader, on_desync = None, debug: bool = False, stacktraces: bool = False) -> System:
    read = tape_reader.read
    bind = tape_reader.bind

    stack = utils.StackFactory()
    current_stack = utils.ThreadLocal([])

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

        if on_desync:
            replay = list(stack())[2:]
            if replay[1:] != this_stack[1:]:
                on_desync(replay, this_stack)
        
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
                if on_desync:
                    on_desync(type, value)
                else:
                    raise ReplayException(f"Unexpected message: {type}, was expecting {value}")

    # sync = functional.repeatedly(expect, "SYNC")

    def run_callback():
        fn = read()
        args = read()
        kwargs = read()
        try:
            fn(*args, **kwargs)
        except ReplayException:
            raise
        except Exception:
            pass
            
    def diff(record, replay):
        if record != replay:
            if on_desync:
                on_desync(record, replay)
            else:
                raise ReplayException(f"Checkpoint difference: {record}, was expecting {replay}")

    def checkpoint(replay):
        expect('CHECKPOINT')
        diff(record = read(), replay = replay)

    def next_result():
        while True:
            type = read()

            if type == "STACKTRACE":
                on_stacktrace()
            elif type == "RESULT":
                return read()
            elif type == "ERROR":
                raise read()
            elif type == "CALL":
                run_callback()
            else:
                if on_desync:
                    on_desync(type, "RESULT", "ERROR", "CALL")
                else:
                    raise ReplayException(f"Unexpected message: {type}, was expecting a result, error, or call")

    if stacktraces:

        def next_stacktrace(obj):
            type = read()
            if type == "STACKTRACE":
                on_stacktrace()
            else:
                raise ReplayException(f"Unexpected message: {type}, was expecting a stacktrace")
            
            return obj

        bind = functional.sequence(next_stacktrace, bind)

    return System(
        primary_hooks = None,
        secondary_hooks = _secondary_hooks(
            sync = functional.repeatedly(expect, "SYNC"),
            checkpoint = checkpoint if debug else None,
        ),
        lifecycle_hooks=LifecycleHooks(
            on_start=functional.repeatedly(expect, "ON_START"),
            on_end=functional.repeatedly(expect, "ON_END")),

        execute=functional.repeatedly(utils.striptraceback(next_result)),
        on_bind=bind,
    )

@contextmanager
def replayer_context(**kwargs):
    system = replayer(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()

# class IO:
#     """Build record/replay contexts for concrete writer/reader objects."""

#     def __init__(self, system: System, *, 
#                  stacktraces: bool = False, 
#                  debug: bool = False):

#         is_passthrough_type = utils.FastTypePredicate(
#             lambda cls: cls in [int, float, str, bytes, bool]
#         ).istypeof

#         if debug:
#             self.normalize = functional.walker(functional.if_then_else(
#                 functional.or_predicate(is_passthrough_type, system.is_bound),
#                 functional.identity,
#                 lambda value: f'Could not normalize value of type: {type(value)}'))
#         else:
#             self.normalize = None

#         self.stacktraces = stacktraces
#         self.system = system
#         self.stackfactory = utils.StackFactory()

#     @property
#     def debug(self):
#         return self.normalize is not None


#     def _secondary_hooks(self, sync, checkpoint):
#         if self.debug:
#             return CallHooks(
#                 on_call=functional.sequence(self._on_call, checkpoint), 
#                 on_result=functional.sequence(self._on_result, checkpoint),
#                 on_error=functional.sequence(functional.positional_param(1), self._on_error, checkpoint))
#         else:
#             return CallHooks(
#                 on_call=sync,
#                 on_result=sync,
#                 on_error=sync,
#             )

#     def writer(self, write, bind):

#         checkpoint = functional.partial(write, "CHECKPOINT")

#         sync = functional.repeatedly(write, "SYNC")

#         on_bind = bind

#         if self.stacktraces:

#             write_stacktrace = \
#                 functional.sequence(
#                     functional.repeatedly(self.stackfactory.delta),
#                     functional.partial(write, "STACKTRACE"))

#             def with_stacktrace(function):
#                 return utils.observer(on_call = write_stacktrace, function = function)

#             on_bind = with_stacktrace(on_bind)
#             sync = with_stacktrace(sync)
#             checkpoint = with_stacktrace(checkpoint)

#         on_call = functional.pack_call(1, functional.partial(write, "CALL"))
#         on_result = functional.partial(write, "RESULT")
#         on_error = functional.sequence(functional.positional_param(1), functional.partial(write, "ERROR"))

#         return self.system.context(
#             primary_hooks=CallHooks(
#                 on_call = on_call,
#                 on_result = on_result,
#                 on_error = on_error,
#             ),
#             secondary_hooks = self._secondary_hooks(
#                 sync = sync,
#                 checkpoint = checkpoint,
#             ),
#             lifecycle_hooks=LifecycleHooks(
#                 on_start=functional.repeatedly(write, "ON_START"),
#                 on_end=functional.repeatedly(write, "ON_END")),
#             on_bind=on_bind,
#             passthrough_proxyref=True,
#         )

#     def reader(self, read, bind, on_desync = None):

#         stack = utils.ThreadLocal([])

#         def on_stacktrace():
#             to_drop, new_frames = read()
#             this_stack = stack.get()
#             del this_stack[:to_drop]
#             this_stack[:0] = [
#                 (frame.filename, frame.lineno)
#                 if isinstance(frame, utils.Stack)
#                 else tuple(frame)
#                 for frame in new_frames
#             ]

#             if on_desync:
#                 replay = list(self.stackfactory())[2:]
#                 if replay[1:] != this_stack[1:]:
#                     on_desync(replay, this_stack)
            
#         def expect(value):
#             type = read()

#             if type != value:
#                 if type == "STACKTRACE":
#                     on_stacktrace()
#                     expect(value)
#                 elif type == "CALL":
#                     run_callback()
#                     expect(value)
#                 else:                    
#                     if on_desync:
#                         on_desync(type, value)
#                     else:
#                         raise Exception(f"Unexpected message: {type}, was expecting {value}")

#         # sync = functional.repeatedly(expect, "SYNC")

#         def run_callback():
#             fn = read()
#             args = read()
#             kwargs = read()
#             try:
#                 fn(*args, **kwargs)
#             except Exception:
#                 pass
                
#         def diff(record, replay):
#             if record != replay:
#                 if on_desync:
#                     on_desync(record, replay)
#                 else:
#                     raise Exception(f"Checkpoint difference: {record}, was expecting {replay}")

#         def checkpoint(replay):
#             expect('CHECKPOINT')
#             diff(record = read(), replay = replay)

#         def next_result():
#             while True:
#                 type = read()

#                 if type == "STACKTRACE":
#                     on_stacktrace()
#                 elif type == "RESULT":
#                     return read()
#                 elif type == "ERROR":
#                     raise read()
#                 elif type == "CALL":
#                     run_callback()
#                 else:
#                     if on_desync:
#                         on_desync(type, "RESULT", "ERROR", "CALL")
#                     else:
#                         raise Exception(f"Unexpected message: {type}, was expecting a result, error, or call")

#         if self.stacktraces:

#             def next_stacktrace(obj):
#                 type = read()
#                 if type == "STACKTRACE":
#                     on_stacktrace()
#                 else:
#                     raise Exception(f"Unexpected message: {type}, was expecting a stacktrace")
                
#                 return obj

#             bind = functional.sequence(next_stacktrace, bind)

#         return self.system.context(
#             primary_hooks = None,
#             secondary_hooks = self._secondary_hooks(
#                 sync = functional.repeatedly(expect, "SYNC"),
#                 checkpoint = checkpoint,
#             ),
#             lifecycle_hooks=LifecycleHooks(
#                 on_start=functional.repeatedly(expect, "ON_START"),
#                 on_end=functional.repeatedly(expect, "ON_END")),

#             execute=functional.repeatedly(utils.striptraceback(next_result)),
#             on_bind=bind,
#         )
