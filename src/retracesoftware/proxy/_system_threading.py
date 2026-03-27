"""Thread inheritance helpers for the gate-based proxy system."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils


def with_context(context, function):
    return utils.observer(
        on_call=lambda *args, **kwargs: context.__enter__(),
        on_result=lambda result: context.__exit__(None, None, None),
        on_error=lambda typ, exc, tb: context.__exit__(typ, exc, tb),
        function=function,
    )


def wrap_start_new_thread(system, original_start_new_thread):
    """Wrap ``start_new_thread`` so child threads inherit active retrace context."""

    def wrap_thread_function(function):
        context = system.current_context.get()
        if system._in_sandbox() and context:
            return with_context(context, function)
        return function

    return functional.positional_param_transform(
        function=original_start_new_thread,
        index=0,
        transform=wrap_thread_function,
    )
