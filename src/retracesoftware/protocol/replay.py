"""Replay-side protocol parsing and reader adapters."""

from collections import deque
from typing import Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from .messages import (
    AsyncNewPatchedMessage,
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    HandleMessage,
    MonitorMessage,
    ResultMessage,
    ThreadSwitchMessage,
)
from retracesoftware.proxy.stubfactory import StubRef

def next_message(source: Callable[[], object]):
    """Read one high-level protocol message from *source*."""

    tag = source()

    if tag == "RESULT":
        return ResultMessage(source())
    if tag == "ERROR":
        return ErrorMessage(source())
    if tag == "ASYNC_CALL":
        return CallMessage(source(), source(), source())
    if tag == "CHECKPOINT":
        return CheckpointMessage(source())
    if tag == "MONITOR":
        return MonitorMessage(source())
    if tag == "ASYNC_NEW_PATCHED":
        return AsyncNewPatchedMessage(source())
    return tag

class ReplayReader:
    """Replay policy layered above the raw protocol parser."""

    def __init__(
        self,
        source: Callable[[], object],
        *,
        bind: Callable[[object], None],
        mark_retraced: Callable[[object], None] | None = None,
        stub_factory=None,
        monitor_enabled: bool = False,
    ):
        self.source = source
        self.type_deserializer = {}
        self._bind = bind
        self._mark_retraced = utils.noop if mark_retraced is None else mark_retraced
        self.stub_factory = stub_factory
        self._monitor_enabled = monitor_enabled
        self._pending_async_new_patched = deque()

    def bind(self, obj):
        return self._bind(obj)

    def mark_retraced(self, obj):
        return self._mark_retraced(obj)

    def _async_new_patched_signature(self, value):
        if isinstance(value, StubRef):
            return ("stub-ref", value.module, value.name)
        if isinstance(value, type):
            return ("instance-type", value)
        if value is None or isinstance(value, (bool, int, float, str, bytes, bytearray, memoryview)):
            return None
        if isinstance(value, (list, tuple, dict)):
            return None
        target_type = getattr(type(value), "__retrace_target_type__", None)
        if target_type is not None:
            return ("stub-instance", target_type)
        return ("instance-type", type(value))

    def _materialize_async_new_patched(self, value):
        deserializer = self.type_deserializer.get(type(value))
        if deserializer is not None:
            return deserializer(value)

        target_type = getattr(type(value), "__retrace_target_type__", None)
        if target_type is not None:
            return value

        cls = value if isinstance(value, type) else type(value)
        factory = self.stub_factory
        if factory is None:
            factory = utils.create_stub_object

        instance = factory(cls)
        if not isinstance(instance, cls):
            raise TypeError(
                f"async_new_patched materializer returned {type(instance)!r} "
                f"for {cls!r}"
            )
        return instance

    def _remember_async_new_patched(self, value):
        materialized = self._materialize_async_new_patched(value)
        self._bind(materialized)
        self._mark_retraced(materialized)
        signature = self._async_new_patched_signature(value)
        self._pending_async_new_patched.append((signature, materialized))
        return materialized

    def _deserialize_result(self, value):
        def transform(item):
            if self._pending_async_new_patched and item is self._pending_async_new_patched[0][1]:
                return self._pending_async_new_patched.popleft()[1]

            signature = self._async_new_patched_signature(item)
            if self._pending_async_new_patched and signature == self._pending_async_new_patched[0][0]:
                return self._pending_async_new_patched.popleft()[1]

            deserializer = self.type_deserializer.get(type(item))
            if deserializer is not None:
                return deserializer(item)
            return item

        return functional.walker(transform)(value)

    def sync(self):
        while True:
            msg = next_message(self.source)
            if msg == "SYNC":
                return
            if isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence

                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) during sync "
                        f"— recording had function calls that replay did not"
                    )
                continue
            if isinstance(msg, AsyncNewPatchedMessage):
                self._remember_async_new_patched(msg.value)
                continue

    def async_call(self, call_message):
        try:
            call_message.fn(*call_message.args, **call_message.kwargs)
        except Exception:
            import traceback
            print(f"exception in async_call: {traceback.format_exc()}")

    @utils.striptraceback
    def read_result(self):
        while True:
            msg = next_message(self.source)

            if isinstance(msg, ResultMessage):
                value = self._deserialize_result(msg.result)
                self._pending_async_new_patched.clear()
                return value
            elif isinstance(msg, ErrorMessage):
                raise msg.error
            elif isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence

                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) in result stream"
                    )
            elif isinstance(msg, AsyncNewPatchedMessage):
                self._remember_async_new_patched(msg.value)
            elif isinstance(msg, CallMessage):
                self.async_call(msg)
            # elif isinstance(msg, CheckpointMessage):
            #     pass
            else:
                raise ValueError(f"unexpected message: {msg}")

    def checkpoint(self, value):
        while True:
            msg = next_message(self.source)
            if isinstance(msg, CheckpointMessage):
                if value != msg.value:
                    from retracesoftware.install import ReplayDivergence

                    raise ReplayDivergence(
                        f"replay divergence: expected {msg.value!r}, "
                        f"got {value!r}"
                    )
                return
            if isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence

                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) during checkpoint"
                    )
                continue
            if isinstance(msg, AsyncNewPatchedMessage):
                self._remember_async_new_patched(msg.value)
                continue
            if isinstance(msg, (CallMessage, ResultMessage, ErrorMessage)):
                continue
            if msg == "SYNC":
                continue

    def monitor_checkpoint(self, value):
        msg = next_message(self.source)
        if not isinstance(msg, MonitorMessage):
            from retracesoftware.install import ReplayDivergence

            raise ReplayDivergence(
                f"expected MONITOR({value!r}), got {type(msg).__name__} "
                f"— replay has function calls that recording did not"
            )
        if value != msg.value:
            from retracesoftware.install import ReplayDivergence

            raise ReplayDivergence(
                f"monitor divergence: expected {msg.value!r}, "
                f"got {value!r}"
            )

__all__ = [
    "AsyncNewPatchedMessage",
    "CallMessage",
    "CheckpointMessage",
    "ErrorMessage",
    "HandleMessage",
    "MonitorMessage",
    "ReplayReader",
    "ResultMessage",
    "ThreadSwitchMessage",
    "next_message",
]
