"""Public record/replay context builders for the gate-based proxy system."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from .context import CallHooks, LifecycleHooks
from ._system_specs import create_context, create_ext_spec, create_int_spec


def _stacktrace_before(enabled, writer, function):
    if not enabled or not hasattr(writer, "stacktrace"):
        return function

    def wrapped(*args, **kwargs):
        writer.stacktrace()
        return function(*args, **kwargs)

    return wrapped


def _normalized_checkpoint(checkpoint, normalize):
    if checkpoint is None or normalize is None:
        return utils.noop

    def wrapped(value):
        return checkpoint(normalize(value))

    return wrapped


def record_context(
    system,
    writer,
    debug=False,
    stacktraces=False,
    normalize=None,
    on_start=None,
    on_end=None,
):
    """Build a recording context for *system* and *writer*."""
    del debug

    bind = utils.runall(
        system.is_bound.add,
        _stacktrace_before(stacktraces, writer, writer.bind),
    )
    write_call = _stacktrace_before(stacktraces, writer, writer.write_call)
    async_call = _stacktrace_before(stacktraces, writer, writer.async_call)
    checkpoint = _normalized_checkpoint(getattr(writer, "checkpoint", None), normalize)

    def write_result(value):
        checkpoint(value)
        return writer.write_result(value)

    def write_error(exc_type, exc_value, exc_tb):
        return writer.write_error(exc_type, exc_value, exc_tb)

    def callback_result(value):
        checkpoint(value)

    def callback_error(exc_type, exc_value, exc_tb):
        checkpoint(exc_value)

    def on_async_new_patched(obj):
        async_call(utils.create_stub_object, type(obj))
        bind(obj)

    int_spec = create_int_spec(
        system,
        bind=bind,
        checkpoint=checkpoint,
        on_call=async_call,
        on_result=callback_result,
        on_error=callback_error,
    )
    ext_spec = create_ext_spec(
        system,
        on_call=write_call,
        on_result=write_result,
        on_error=write_error,
        track=bind,
    )

    return create_context(
        system,
        int_spec=int_spec,
        ext_spec=ext_spec,
        bind=bind,
        async_new_patched=on_async_new_patched,
        on_start=on_start,
        on_end=on_end,
    )


def replay_context(
    system,
    reader,
    normalize=None,
    on_start=None,
    on_end=None,
    **_kwargs,
):
    """Build a replay context for *system* and *reader*."""
    checkpoint = _normalized_checkpoint(getattr(reader, "checkpoint", None), normalize)

    def callback_result(value):
        checkpoint(value)

    def callback_error(exc_type, exc_value, exc_tb):
        checkpoint(exc_value)

    bind = utils.runall(system.is_bound.add, reader.bind)

    int_spec = create_int_spec(
        system,
        bind=bind,
        checkpoint=checkpoint,
        on_call=None,
        on_result=callback_result,
        on_error=callback_error,
    )
    ext_spec = create_ext_spec(
        system,
        on_call=reader.write_call,
        on_result=checkpoint,
        on_error=callback_error,
        track=bind,
    )

    return create_context(
        system,
        int_spec=int_spec,
        ext_spec=ext_spec,
        ext_runner=functional.repeatedly(reader.read_result),
        bind=bind,
        async_new_patched=bind,
        on_start=on_start,
        on_end=on_end,
    )


__all__ = ["record_context", "replay_context"]
