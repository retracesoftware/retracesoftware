"""Protocol-level message objects used by record/replay.

These types represent semantic protocol events above the raw stream
layer. They are intentionally small transport-neutral containers.
"""


class ResultMessage:
    __slots__ = ("result",)

    def __init__(self, result):
        self.result = result


class ErrorMessage:
    __slots__ = ("error",)

    def __init__(self, error):
        self.error = error


class CallMessage:
    __slots__ = ("fn", "args", "kwargs")

    def __init__(self, fn, args, kwargs):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs


class CheckpointMessage:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class MonitorMessage:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class AsyncNewPatchedMessage:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class ThreadSwitchMessage:
    """Marker used by the in-memory protocol tape when thread ownership changes."""

    __slots__ = ("thread_id",)

    def __init__(self, thread_id):
        self.thread_id = thread_id

    def __repr__(self):
        return f"ThreadSwitchMessage({self.thread_id!r})"


class HandleMessage:
    """A named side-channel handle write on the protocol tape."""

    __slots__ = ("name", "value")

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __repr__(self):
        return f"HandleMessage({self.name!r}, {self.value!r})"
