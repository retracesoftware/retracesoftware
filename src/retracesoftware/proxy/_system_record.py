"""Record-context policy helpers for the gate-based proxy system."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.stubfactory import StubRef


def record_context(system, writer, normalize=None, stacktraces=False):
    """Build the recording gate context for *system*."""

    checkpoint = functional.sequence(normalize, writer.checkpoint) if normalize else None

    def write_internal_call(_fn, *args, **kwargs):
        if __import__("os").environ.get("RETRACE_CALLBACK_TRACE"):
            import sys

            name = getattr(_fn, "__qualname__", getattr(_fn, "__name__", repr(_fn)))
            arg_types = tuple(type(arg).__name__ for arg in args)
            kwarg_types = {key: type(value).__name__ for key, value in kwargs.items()}
            sys.stderr.write(
                f"retrace-callback fn={name} arg_types={arg_types} kwarg_types={kwarg_types}\n"
            )
            sys.stderr.flush()
        writer.async_call(*args, **kwargs)

    if stacktraces:
        def write_stack_then(*args, **kwargs):
            writer.stacktrace()

        ext_on_call = utils.chain(write_stack_then, writer.sync)
        int_on_call = write_internal_call
    else:
        ext_on_call = writer.sync
        int_on_call = write_internal_call

    remember_bind = utils.runall(writer.bind, system.is_bound.add)
    intern = utils.runall(writer.intern, system.is_bound.add)

    def register_type_serializer(proxytype, cls):
        stub_ref = StubRef(cls)
        intern(stub_ref)
        writer.type_serializer[proxytype] = functional.constantly(stub_ref)

    def remember_async_new_patched(obj):
        assert system.is_bound(type(obj))
        writer.async_new_patched(type(obj))

    def track(value):
        def visit(obj):
            if type(obj) in system.patched_types and not system.is_bound(obj):
                writer.async_new_patched(type(obj))
                remember_bind(obj)
            return obj

        return functional.walker(visit)(value)

    return system._create_context(
        _async_new_patched=remember_async_new_patched,
        _bind=remember_bind,
        int_spec=system._create_int_spec(
            bind=remember_bind,
            on_call=int_on_call,
            on_result=checkpoint,
            on_error=checkpoint,
        ),
        ext_spec=system._create_ext_spec(
            sync=ext_on_call,
            track=track,
            on_result=utils.chain(writer.write_result, checkpoint),
            on_error=utils.chain(writer.write_error, checkpoint),
            on_new_proxytype=register_type_serializer,
        ),
    )
