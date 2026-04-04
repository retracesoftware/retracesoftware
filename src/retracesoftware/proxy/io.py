"""IO bridge helpers for the gate-based ``System``."""
import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System

class IO:
    """Build record/replay contexts for concrete writer/reader objects."""

    def __init__(self, system: System, *, 
                 stacktraces: bool = False, 
                 debug: bool = False):

        is_passthrough_type = utils.FastTypePredicate(
            lambda cls: cls in [int, float, str, bytes, bool]
        ).istypeof

        if debug:
            self.normalize = functional.walker(functional.if_then_else(
                functional.or_predicate(is_passthrough_type, system.is_bound),
                functional.identity,
                lambda value: f'Could not normalize value of type: {type(value)}'))
        else:
            self.normalize = None

        self.stacktraces = stacktraces
        self.system = system
        self.stackfactory = utils.StackFactory()

    @property
    def debug(self):
        return self.normalize is not None

    def _on_call(self, fn, *args, **kwargs):
        assert self.system.is_bound(fn)
        
        return {
            "function": fn,
            "args": self.normalize(args),
            "kwargs": self.normalize(kwargs),
        }

    def _on_result(self, result):
        return {"result": self.normalize(result)}

    def _on_error(self, error):
        return {"error": self.normalize(error)}

    def _secondary_hooks(self, sync, checkpoint):
        if self.debug:
            return CallHooks(
                on_call=functional.sequence(self._on_call, checkpoint), 
                on_result=functional.sequence(self._on_result, checkpoint),
                on_error=functional.sequence(functional.positional_param(1), self._on_error, checkpoint))
        else:
            return CallHooks(
                on_call=sync,
                on_result=sync,
                on_error=sync,
            )

    def writer(self, write, bind):

        checkpoint = functional.partial(write, "CHECKPOINT")

        sync = functional.repeatedly(write, "SYNC")

        on_bind = bind

        if self.stacktraces:

            write_stacktrace = \
                functional.sequence(
                    functional.repeatedly(self.stackfactory.delta),
                    functional.partial(write, "STACKTRACE"))

            def with_stacktrace(function):
                return utils.observer(on_call = write_stacktrace, function = function)

            on_bind = with_stacktrace(on_bind)
            sync = with_stacktrace(sync)
            checkpoint = with_stacktrace(checkpoint)

        on_call = functional.pack_call(1, functional.partial(write, "CALL"))
        on_result = functional.partial(write, "RESULT")
        on_error = functional.sequence(functional.positional_param(1), functional.partial(write, "ERROR"))

        return self.system.context(
            primary_hooks=CallHooks(
                on_call = on_call,
                on_result = on_result,
                on_error = on_error,
            ),
            secondary_hooks = self._secondary_hooks(
                sync = sync,
                checkpoint = checkpoint,
            ),
            lifecycle_hooks=LifecycleHooks(
                on_start=functional.repeatedly(write, "ON_START"),
                on_end=functional.repeatedly(write, "ON_END")),
            on_bind=on_bind,
            passthrough_proxyref=True,
        )

    def reader(self, read, bind, on_desync = None):

        stack = utils.ThreadLocal([])

        def on_stacktrace():
            to_drop, new_frames = read()
            this_stack = stack.get()
            del this_stack[:to_drop]
            this_stack[:0] = [
                (frame.filename, frame.lineno)
                if isinstance(frame, utils.Stack)
                else tuple(frame)
                for frame in new_frames
            ]

            if on_desync:
                replay = list(self.stackfactory())[2:]
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
                        raise Exception(f"Unexpected message: {type}, was expecting {value}")

        # sync = functional.repeatedly(expect, "SYNC")

        def run_callback():
            fn = read()
            args = read()
            kwargs = read()
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
                
        def diff(record, replay):
            if record != replay:
                if on_desync:
                    on_desync(record, replay)
                else:
                    raise Exception(f"Checkpoint difference: {record}, was expecting {replay}")

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
                        raise Exception(f"Unexpected message: {type}, was expecting a result, error, or call")

        if self.stacktraces:

            def next_stacktrace(obj):
                type = read()
                if type == "STACKTRACE":
                    on_stacktrace()
                else:
                    raise Exception(f"Unexpected message: {type}, was expecting a stacktrace")
                
                return obj

            bind = functional.sequence(next_stacktrace, bind)

        return self.system.context(
            primary_hooks = None,
            secondary_hooks = self._secondary_hooks(
                sync = functional.repeatedly(expect, "SYNC"),
                checkpoint = checkpoint,
            ),
            lifecycle_hooks=LifecycleHooks(
                on_start=functional.repeatedly(expect, "ON_START"),
                on_end=functional.repeatedly(expect, "ON_END")),

            execute=functional.repeatedly(utils.striptraceback(next_result)),
            on_bind=bind,
        )
