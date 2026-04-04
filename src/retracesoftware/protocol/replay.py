"""Replay-side protocol parsing and reader adapters."""

from collections import deque
from pathlib import Path
from typing import Callable
import os

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
    StacktraceMessage,
    ThreadSwitchMessage,
)
from .normalize import normalize as normalize_checkpoint_value
from .record import CALL


_PACKAGE_ROOT = os.path.normcase(
    os.path.abspath(str(Path(__file__).resolve().parents[1]))
)

def _materialize_stack_delta(delta):
    to_drop, frames = delta
    return (to_drop, tuple(tuple(frame) for frame in frames))


def _looks_like_stacktrace_delta(value):
    if not (isinstance(value, tuple) and len(value) == 2):
        return False
    num_frames_to_drop, new_frames = value
    if not isinstance(num_frames_to_drop, int) or not isinstance(new_frames, tuple):
        return False
    for frame_group in new_frames:
        if not isinstance(frame_group, tuple):
            return False
        for frame in frame_group:
            if not (
                isinstance(frame, tuple)
                and len(frame) == 2
                and isinstance(frame[0], str)
                and isinstance(frame[1], int)
            ):
                return False
    return True


def _normalize_stack_for_compare(stack):
    normalized = []
    for filename, lineno in stack:
        if not isinstance(filename, str):
            continue
        abs_filename = os.path.normcase(os.path.abspath(filename))
        if abs_filename.startswith(_PACKAGE_ROOT + os.sep):
            continue
        normalized.append((filename, lineno))
    return tuple(normalized)


def _format_stack_for_message(stack, limit=8):
    if not stack:
        return "(empty)"
    tail = stack[-limit:]
    return " | ".join(f"{filename}:{lineno}" for filename, lineno in tail)


def _first_stack_difference(recorded, replay):
    limit = min(len(recorded), len(replay))
    for index in range(limit):
        if recorded[index] != replay[index]:
            return index, recorded[index], replay[index]
    if len(recorded) != len(replay):
        return limit, (
            recorded[limit] if limit < len(recorded) else None
        ), (
            replay[limit] if limit < len(replay) else None
        )
    return None, None, None


class StacktraceFactory:
    def __init__(self):
        self._previous = ()

    def materialize(self, num_frames_to_drop, new_frames):
        normalized_frames = tuple(tuple(frame) for frame in new_frames)
        if normalized_frames:
            stack = tuple(normalized_frames[0])
        elif not num_frames_to_drop:
            stack = self._previous
        else:
            stack = self._previous[num_frames_to_drop:]
        self._previous = stack
        return StacktraceMessage(stack)


def next_message(source: Callable[[], object], stacktrace_factory):
    """Read one high-level protocol message from *source*."""

    tag = source()

    if tag == "STACKTRACE":
        return stacktrace_factory(source(), source())
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
        stacktrace_factory=None,
    ):
        self.source = source
        self.type_deserializer = {}
        self._bind = bind
        self._mark_retraced = utils.noop if mark_retraced is None else mark_retraced
        self.stub_factory = stub_factory
        self._monitor_enabled = monitor_enabled
        self._pending_async_new_patched = deque()
        self._debug_history_enabled = os.getenv("RETRACE_DEBUG_REPLAY_HISTORY") == "1"
        self._debug_history = deque(maxlen=40)
        self._recorded_stacktrace_factory = StacktraceFactory()
        self._replay_stacktrace_factory = StacktraceFactory()
        self._live_stacktrace_factory = stacktrace_factory

    def _next_message(self, source: Callable[[], object]):
        return next_message(source, stacktrace_factory=self._recorded_stacktrace_factory.materialize)

    def _get_stacktrace_factory(self):
        if self._live_stacktrace_factory is None:
            self._live_stacktrace_factory = utils.StackFactory()
        return self._live_stacktrace_factory

    def _capture_replay_stack_delta(self):
        return self._replay_stacktrace_factory.materialize(
            *_materialize_stack_delta(self._get_stacktrace_factory().delta()),
        )

    def _coerce_recorded_stacktrace(self, value):
        if isinstance(value, StacktraceMessage):
            return value
        if _looks_like_stacktrace_delta(value):
            return self._recorded_stacktrace_factory.materialize(*value)
        raise TypeError(f"expected stacktrace delta, got {value!r}")

    def _diff_stacktrace(self, recorded_msg, *, context):
        recorded_msg = self._coerce_recorded_stacktrace(recorded_msg)
        replay_msg = self._capture_replay_stack_delta()

        recorded = _normalize_stack_for_compare(recorded_msg.stacktrace)
        replay = _normalize_stack_for_compare(replay_msg.stacktrace)
        if recorded == replay:
            return

        from retracesoftware.install import ReplayDivergence

        detail = ""
        if os.getenv("RETRACE_DEBUG_STACK_DIFF") == "1":
            index, recorded_frame, replay_frame = _first_stack_difference(recorded, replay)
            detail = (
                f" [len(recorded)={len(recorded)} len(replay)={len(replay)}"
                f" first_diff={index} recorded_frame={recorded_frame!r}"
                f" replay_frame={replay_frame!r}]"
            )

        raise ReplayDivergence(
            "replay stacktrace divergence during "
            f"{context}: recorded={_format_stack_for_message(recorded)} "
            f"replay={_format_stack_for_message(replay)}"
            f"{detail}{self._debug_suffix()}"
        )

    def handle_stacktrace(self, msg, *, context="replay"):
        if isinstance(msg, StacktraceMessage):
            recorded_msg = msg
        elif msg == "STACKTRACE":
            recorded_msg = self._recorded_stacktrace_factory.materialize(
                self.source(),
                self.source(),
            )
        else:
            raise TypeError(f"expected STACKTRACE, got {msg!r}")

        self._diff_stacktrace(recorded_msg, context=context)

    def bind(self, obj):
        return self._bind(obj)

    def _note_debug(self, context, msg):
        if not self._debug_history_enabled:
            return
        self._debug_history.append((context, self._format_debug_msg(msg)))

    def _debug_suffix(self):
        if not self._debug_history_enabled or not self._debug_history:
            return ""
        tail = " | ".join(f"{context}: {msg}" for context, msg in self._debug_history)
        return f" | history={tail}"

    @staticmethod
    def _format_debug_msg(msg):
        if isinstance(msg, CallMessage):
            return (
                f"CallMessage(fn={msg.fn!r}, args={msg.args!r}, kwargs={msg.kwargs!r})"
            )
        if isinstance(msg, ResultMessage):
            return f"ResultMessage(result={msg.result!r})"
        if isinstance(msg, ErrorMessage):
            return f"ErrorMessage(error={msg.error!r})"
        if isinstance(msg, StacktraceMessage):
            return f"StacktraceMessage(stacktrace={msg.stacktrace!r})"
        if isinstance(msg, HandleMessage):
            return f"HandleMessage({msg.name!r}, {msg.value!r})"
        return repr(msg)

    def mark_retraced(self, obj):
        return self._mark_retraced(obj)

    def _async_new_patched_signature(self, value):
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

    def _advance_until(self, marker):
        while True:
            msg = self._next_message(self.source)
            self._note_debug(f"advance_to_{marker}", msg)
            if msg == marker:
                return
            if isinstance(msg, StacktraceMessage):
                self.handle_stacktrace(msg, context=f"advance_to_{marker}")
                continue
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
            from retracesoftware.install import ReplayDivergence

            raise ReplayDivergence(
                f"unexpected {self._format_debug_msg(msg)} while advancing to "
                f"{marker}{self._debug_suffix()}"
            )

    def sync(self):
        self._advance_until("SYNC")

    def write_call(self, *args, **kwargs):
        self._advance_until(CALL)

    def on_call(self, *args, **kwargs):
        return self.write_call(*args, **kwargs)

    def async_call(self, fn, *args, **kwargs):
        if os.getenv("RETRACE_DEBUG_CALLBACK_FLOW") == "1":
            print(
                "retrace debug: async_call enter "
                f"fn={utils.try_unwrap(fn)!r} "
                f"arg_types={tuple(type(arg).__name__ for arg in args)}",
                file=os.sys.stderr,
            )
        # Replay callbacks regenerate sandbox side effects only.
        # If an exception is supposed to cross back into the sandbox,
        # it must come from a recorded ERROR message, not from the live
        # callback execution here.
        try:
            fn(*args, **kwargs)
            if os.getenv("RETRACE_DEBUG_CALLBACK_FLOW") == "1":
                print(
                    "retrace debug: async_call exit "
                    f"fn={utils.try_unwrap(fn)!r}",
                    file=os.sys.stderr,
                )
        except Exception as exc:
            from retracesoftware.install import ReplayDivergence

            if isinstance(exc, ReplayDivergence):
                if os.getenv("RETRACE_DEBUG_CALLBACK_FLOW") == "1":
                    print(
                        "retrace debug: async_call divergence "
                        f"fn={utils.try_unwrap(fn)!r}: {exc!r}",
                        file=os.sys.stderr,
                    )
                raise
            if os.getenv("RETRACE_DEBUG_CALLBACK_ERRORS") == "1":
                print(
                    f"retrace debug: async_call swallowed {exc.__class__.__name__}"
                    f" from {fn!r}: {exc!r}",
                    file=os.sys.stderr,
                )
            pass

    @utils.striptraceback
    def read_result(self):
        while True:
            msg = self._next_message(self.source)

            self._note_debug("read_result", msg)

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
            elif isinstance(msg, StacktraceMessage):
                self.handle_stacktrace(msg, context="read_result")
                continue
            elif isinstance(msg, AsyncNewPatchedMessage):
                self._remember_async_new_patched(msg.value)
            elif isinstance(msg, CallMessage):
                self.async_call(
                    self._deserialize_result(msg.fn),
                    *self._deserialize_result(msg.args),
                    **self._deserialize_result(msg.kwargs))
            # elif isinstance(msg, CheckpointMessage):
            #     pass
            else:
                raise ValueError(f"unexpected message: {msg}{self._debug_suffix()}")

    def checkpoint(self, value):
        while True:
            msg = self._next_message(self.source)

            self._note_debug("checkpoint", msg)
            if isinstance(msg, CheckpointMessage):
                value = normalize_checkpoint_value(value)
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
            if isinstance(msg, StacktraceMessage):
                self.handle_stacktrace(msg, context="checkpoint")
                continue
            if isinstance(msg, AsyncNewPatchedMessage):
                self._remember_async_new_patched(msg.value)
                continue
            if isinstance(msg, (CallMessage, ResultMessage, ErrorMessage)):
                continue
            if msg in ("SYNC", CALL):
                continue

    def monitor_checkpoint(self, value):
        msg = self._next_message(self.source)

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
    "CALL",
    "ErrorMessage",
    "HandleMessage",
    "MonitorMessage",
    "ReplayReader",
    "ResultMessage",
    "ThreadSwitchMessage",
    "next_message",
]
