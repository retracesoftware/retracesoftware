"""Protocol-level message objects used by record/replay.

These types represent semantic protocol events above the raw stream
layer. They are intentionally small transport-neutral containers.
"""


class ProtocolMessage:
    __slots__ = ("thread_id",)

    def __init__(self, *, thread_id=None):
        self.thread_id = thread_id


class StacktraceMessage(ProtocolMessage):
    __slots__ = ("stacktrace",)

    def __init__(self, stacktrace, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.stacktrace = stacktrace


class ResultMessage(ProtocolMessage):
    __slots__ = ("result",)

    def __init__(self, result, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.result = result


class ErrorMessage(ProtocolMessage):
    __slots__ = ("error",)

    def __init__(self, error, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.error = error


class CallMessage(ProtocolMessage):
    __slots__ = ("fn", "args", "kwargs")

    def __init__(self, fn, args, kwargs, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs


class CheckpointMessage(ProtocolMessage):
    __slots__ = ("value",)

    def __init__(self, value, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.value = value


class MonitorMessage(ProtocolMessage):
    __slots__ = ("value",)

    def __init__(self, value, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.value = value


class AsyncNewPatchedMessage(ProtocolMessage):
    __slots__ = ("value",)

    def __init__(self, value, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.value = value


class ThreadSwitchMessage(ProtocolMessage):
    """Marker used by the in-memory protocol tape when thread ownership changes."""

    __slots__ = ()

    def __init__(self, thread_id):
        super().__init__(thread_id=thread_id)

    def __repr__(self):
        return f"ThreadSwitchMessage({self.thread_id!r})"


class HandleMessage(ProtocolMessage):
    """A named side-channel handle write on the protocol tape."""

    __slots__ = ("name", "value")

    def __init__(self, name, value, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.name = name
        self.value = value

    def __repr__(self):
        if self.thread_id is None:
            return f"HandleMessage({self.name!r}, {self.value!r})"
        return (
            f"HandleMessage({self.name!r}, {self.value!r}, "
            f"thread_id={self.thread_id!r})"
        )
