import os

import retracesoftware.utils as utils

from retracesoftware.install.edgecases import (
    multiprocessing_finalize_call,
    multiprocessing_resource_tracker_pthread_sigmask,
)


def test_multiprocessing_finalize_call_uses_live_pid_guard():
    registry = {}
    callback_calls = []

    def recorded_getpid():
        raise AssertionError("recorded getpid should not be used by finalizers")

    def sub_debug(*args):
        return None

    def target(
        self,
        wr=None,
        _finalizer_registry=registry,
        sub_debug=sub_debug,
        getpid=recorded_getpid,
    ):
        try:
            del _finalizer_registry[self._key]
        except KeyError:
            sub_debug("finalizer no longer registered")
        else:
            if self._pid != getpid():
                sub_debug("finalizer ignored because different process")
                return None
            return self._callback(*self._args, **self._kwargs)
        return None

    class Finalizer:
        _key = (None, 1)
        _pid = os.getpid() + 1
        _callback = callback_calls.append
        _args = ("cleanup",)
        _kwargs = {}

    finalizer = Finalizer()
    registry[finalizer._key] = finalizer

    wrapped = multiprocessing_finalize_call(target)

    assert wrapped(finalizer) is None
    assert finalizer._key not in registry
    assert callback_calls == []


def test_resource_tracker_pthread_sigmask_wrapper_is_stack_scoped():
    calls = []

    def original(*args):
        calls.append(("original", args))
        return "original"

    def handler(wrapped, *args, **kwargs):
        calls.append(("patched", args))
        return "patched"

    patched = utils.wrapped_callable(
        utils.wrapped_function(target=original, handler=handler)
    )
    wrapped = multiprocessing_resource_tracker_pthread_sigmask(patched)

    assert wrapped("outside") == "patched"

    namespace = {
        "__name__": "multiprocessing.resource_tracker",
        "wrapped": wrapped,
    }
    exec(
        "def ensure_running():\n"
        "    return wrapped('inside')\n",
        namespace,
    )

    assert namespace["ensure_running"]() == "original"
    assert calls == [
        ("patched", ("outside",)),
        ("original", ("inside",)),
    ]
