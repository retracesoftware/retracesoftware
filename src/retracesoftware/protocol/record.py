"""Protocol helpers for the recording side of retrace."""

from types import SimpleNamespace


def stream_writer(writer, stackfactory=None, on_write_error=None):
    """Adapt a ``stream.writer`` to the high-level writer protocol.

    The returned object exposes semantic protocol operations such as
    ``write_result`` and ``async_call`` while delegating transport details to
    the underlying stream writer.
    """
    if stackfactory:
        stacktrace_handle = writer.handle("STACKTRACE")

        def stacktrace():
            stacktrace_handle(stackfactory.delta())
    else:
        stacktrace = None

    _write_error = writer.handle("ERROR")

    def write_error(exc_type, exc_value, exc_tb):
        _write_error(exc_value)

    def bind_write_error(func):
        from retracesoftware import utils

        if on_write_error:
            return utils.observer(function=func, on_error=on_write_error)
        return func

    _async_call = writer.handle("ASYNC_CALL")

    def async_call(fn, *args, **kwargs):
        # Stream-backed writers expose handle-based call sites that accept
        # positional payloads only. Serialize internal callback kwargs as the
        # second ASYNC_CALL payload instead of forwarding them as Python kwargs.
        return _async_call(fn, args, kwargs)

    return SimpleNamespace(
        type_serializer=writer.type_serializer,
        sync=bind_write_error(writer.handle("SYNC")),
        write_result=bind_write_error(writer.handle("RESULT")),
        write_error=bind_write_error(write_error),
        bind=bind_write_error(writer.bind),
        intern=bind_write_error(writer.intern),
        async_new_patched=bind_write_error(writer.async_new_patched),
        async_call=bind_write_error(async_call),
        checkpoint=bind_write_error(writer.handle("CHECKPOINT")),
        stacktrace=bind_write_error(stacktrace),
    )


__all__ = ["stream_writer"]
