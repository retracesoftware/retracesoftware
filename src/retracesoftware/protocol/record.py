"""Protocol helpers for the recording side of retrace."""

from types import SimpleNamespace
from retracesoftware import utils
from retracesoftware import functional
from .normalize import normalize


CALL = "CALL"

def _materialize_stack_delta(delta):
    to_drop, frames = delta
    # Convert live Stack snapshots to inert location tuples before they
    # cross the async writer queue. Otherwise the background writer /
    # return thread can end up owning Stack lifetimes and deallocating
    # objects retained by those snapshots off the originating thread.
    return (to_drop, tuple(tuple(frame) for frame in frames))


def stream_writer(writer, stackfactory=None, on_write_error=None, debug = False):
    """Adapt a ``stream.writer`` to the high-level writer protocol.

    The returned object exposes semantic protocol operations such as
    ``write_result`` and ``async_call`` while delegating transport details to
    the underlying stream writer.
    """
        
    checkpoint_handle = writer.handle("CHECKPOINT")

    stacktrace = None
    if stackfactory is not None:
        stacktrace_handle = writer.handle("STACKTRACE")

        def stacktrace():
            stacktrace_handle(_materialize_stack_delta(stackfactory.delta()))

    _write_error = writer.handle("ERROR")

    def write_error(exc_type, exc_value, exc_tb):
        _write_error(exc_value)

    def bind_write_error(func):
        if on_write_error:
            return utils.observer(function=func, on_error=on_write_error)
        return func

    async_call_handle = writer.handle("ASYNC_CALL")

    def async_call(fn, *args, **kwargs):
        # Stream-backed writers expose handle-based call sites that accept
        # positional payloads only. Serialize internal callback kwargs as the
        # second ASYNC_CALL payload instead of forwarding them as Python kwargs.
        return async_call_handle(fn, args, kwargs)

    def checkpoint(value):
        if stacktrace is not None:
            stacktrace()
        checkpoint_handle(normalize(value))

    call = functional.repeatedly(writer.handle(CALL))
    bind = writer.bind

    if debug:
        def on_bind(obj):
           checkpoint({'type': 'on_bind', 'bound': obj})

        bind = utils.runall(on_bind, bind)

        def on_call(fn, *args, **kwargs):
            checkpoint({'type': 'on_call',
                        'fn': fn,
                        'args': args, 
                        'kwargs': kwargs})

        call = functional.runall(on_call, call)

        def on_async_call(fn, *args, **kwargs):
            checkpoint({'type': 'on_async_call',
                        'fn': fn,
                        'args': args, 
                        'kwargs': kwargs})

        async_call = functional.runall(on_async_call, async_call)

    intern = getattr(writer, "intern", None)
    if intern is None:
        intern = getattr(writer, "_intern")

    return SimpleNamespace(
        type_serializer=writer.type_serializer,
        sync = bind_write_error(writer.handle("SYNC")),
        write_call = bind_write_error(call),
        write_result = bind_write_error(writer.handle("RESULT")),
        write_error = bind_write_error(write_error),
        bind = bind_write_error(bind),
        intern = bind_write_error(intern),
        async_new_patched = bind_write_error(writer.async_new_patched),
        async_call = bind_write_error(async_call),
        checkpoint = bind_write_error(checkpoint),
        stacktrace = bind_write_error(stacktrace),
    )


__all__ = ["CALL", "stream_writer"]
