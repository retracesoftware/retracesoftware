from collections import deque
import functools
import os
import sys
import threading

import retracesoftware.stream as stream

from retracesoftware.gateway._dynamicproxy import is_proxy_ref
from retracesoftware.install.monitoring import (
    begin_suppress_monitoring,
    end_suppress_monitoring,
)
from retracesoftware.proxy.taggedtraceio import (
    TaggedTraceReader,
    next_message,
)
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallMessage,
    CallMarkerMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    ErrorMessage,
    OnStartMessage,
    ResultMessage,
    RunCompletedMessage,
    StacktraceMessage,
    SyncMessage,
    ThreadSwitchMessage,
)
from retracesoftware.stream.reader import ExpectedBindMarker


def _read(source):
    next_method = getattr(source, "next", None)
    if next_method is not None:
        return next_method()

    read_method = getattr(source, "read", None)
    if read_method is not None:
        return read_method()

    if callable(source):
        return source()

    return next(source)


_SCHED_DEBUG = bool(os.environ.get("RETRACE_THREAD_SCHEDULE_DEBUG"))
_DEFAULT_CURSOR = object()


def _debug_scheduler(message):
    if _SCHED_DEBUG:
        print(f"Retrace scheduler: {message}", file=sys.stderr, flush=True)


def _probe_callback_name(kind):
    return f"thread_{kind}"


def _get_thread_callback(probe, kind):
    previous_count = begin_suppress_monitoring()
    try:
        callbacks = getattr(probe, "callbacks", None)
        if callbacks is not None:
            return getattr(callbacks, _probe_callback_name(kind), None)
        getter = getattr(probe, f"get_thread_{kind}_callback", None)
        if getter is None:
            return None
        return getter()
    finally:
        end_suppress_monitoring(previous_count)


def _set_thread_callback(probe, kind, callback):
    previous_count = begin_suppress_monitoring()
    try:
        callbacks = getattr(probe, "callbacks", None)
        if callbacks is not None:
            name = _probe_callback_name(kind)
            if not hasattr(callbacks, name):
                return False
            setattr(callbacks, name, callback)
            return True
        setter = getattr(probe, f"set_thread_{kind}_callback", None)
        if setter is None:
            return False
        setter(callback)
        return True
    finally:
        end_suppress_monitoring(previous_count)


def _probe_call_at(probe, *args):
    call_at = getattr(probe, "call_at", None)
    if call_at is not None:
        return call_at(*args)
    return probe.set_replay_checkpoint(*args)


def _retrace_callback(probe, callback):
    if probe is None:
        return callback
    exclude = getattr(probe, "exclude", None)
    if exclude is not None:
        return exclude(callback)

    disable = getattr(probe, "disable", None)
    if disable is None:
        return callback

    @functools.wraps(callback)
    def disabled_callback(*args, **kwargs):
        return disable(callback, *args, **kwargs)

    return disabled_callback


class PeekableStream:
    __slots__ = ("source", "_buffer", "_close")

    def __init__(self, source):
        self.source = source
        self._buffer = deque()
        self._close = getattr(source, "close", None)

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        if self._buffer:
            return self._buffer.popleft()
        return _read(self.source)

    def peek(self, offset=0):
        while len(self._buffer) <= offset:
            self._buffer.append(_read(self.source))
        return self._buffer[offset]

    def close(self):
        if self._close is not None:
            return self._close()


class MessageStream(TaggedTraceReader):
    __slots__ = ()


def _resolve_value(value, bindings):
    if isinstance(value, stream.Binding):
        return _resolve_value(bindings[value.handle], bindings)
    if is_proxy_ref(value):
        return value()
    if isinstance(value, tuple):
        changed = False
        items = []
        for item in value:
            resolved = _resolve_value(item, bindings)
            changed = changed or resolved is not item
            items.append(resolved)
        return tuple(items) if changed else value
    if isinstance(value, list):
        changed = False
        items = []
        for item in value:
            resolved = _resolve_value(item, bindings)
            changed = changed or resolved is not item
            items.append(resolved)
        return items if changed else value
    if isinstance(value, dict):
        changed = False
        items = {}
        for key, item in value.items():
            resolved_key = _resolve_value(key, bindings)
            resolved = _resolve_value(item, bindings)
            changed = changed or resolved_key is not key or resolved is not item
            items[resolved_key] = resolved
        return items if changed else value
    return value


def _resolve_message(message, bindings):
    thread_id = _resolve_value(getattr(message, "thread_id", None), bindings)

    if isinstance(message, CallMessage):
        return type(message)(
            _resolve_value(message.fn, bindings),
            _resolve_value(message.args, bindings),
            _resolve_value(message.kwargs, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, ResultMessage):
        return type(message)(
            _resolve_value(message.result, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, ErrorMessage):
        return type(message)(
            _resolve_value(message.error, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, CheckpointMessage):
        return type(message)(
            _resolve_value(message.cursor_delta, bindings),
            _resolve_value(message.value, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, StacktraceMessage):
        return type(message)(
            _resolve_value(message.stacktrace, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, ThreadSwitchMessage):
        return type(message)(
            _resolve_value(message.cursor_delta, bindings),
            thread_id=thread_id,
        )
    if isinstance(
        message,
        (
            CallMarkerMessage,
            OnStartMessage,
            RunCompletedMessage,
            SyncMessage,
        ),
    ):
        return type(message)(thread_id=thread_id)
    return _resolve_value(message, bindings)


class BindingStream:
    __slots__ = ("source", "_bindings", "_close")

    def __init__(self, source):
        self.source = source
        self._bindings = {}
        self._close = getattr(source, "close", None)

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def consume_pending_closes(self, *, ignore_end_of_stream=False, buffered_only=False):
        while True:
            try:
                message = self.source.peek()
            except (LookupError, StopIteration):
                return
            except RuntimeError:
                if ignore_end_of_stream:
                    return
                raise

            if not isinstance(message, BindCloseMessage):
                return

            self.source.next()
            self._bindings.pop(message.handle, None)

    def _consume_pending_closes(self):
        self.consume_pending_closes()

    def next(self):
        self._consume_pending_closes()
        message = self.source.next()
        if isinstance(message, BindOpenMessage):
            raise RuntimeError("bind marker returned when bind was expected")
        return _resolve_message(message, self._bindings)

    read = next

    def peek(self, offset=0):
        shadow_bindings = dict(self._bindings)

        while True:
            message = self.source.peek(offset)
            if isinstance(message, BindCloseMessage):
                shadow_bindings.pop(message.handle, None)
                offset += 1
                continue
            if isinstance(message, BindOpenMessage):
                raise RuntimeError("bind marker returned when bind was expected")
            return _resolve_message(message, shadow_bindings)

    def bind(self, obj):
        self._consume_pending_closes()
        message = self.source.peek()
        if not isinstance(message, BindOpenMessage):
            raise ExpectedBindMarker(message)
        self.source.next()
        self._bindings[message.handle] = obj

    def bind_handle(self, handle, obj):
        self._bindings[handle] = obj

    def lookup_handle(self, handle):
        return self._bindings[handle]

    def delete_handle(self, handle):
        self._bindings.pop(handle, None)

    def resolve(self, value):
        return _resolve_value(value, self._bindings)

    def close(self):
        if self._close is not None:
            return self._close()


class SchedulerStream:
    __slots__ = (
        "source",
        "probe",
        "handoff",
        "_active",
        "_callbacks_installed",
        "_close",
        "_current_thread_id",
        "_cursors",
        "_disable_for",
        "_lock",
        "_on_switch",
        "_old_thread_switch_callback",
        "_should_schedule",
        "_thread_id",
    )

    def __init__(
        self,
        source,
        set_callback=None,
        probe=None,
        handoff=None,
        *,
        initial_thread_id=None,
        current_thread_id=None,
        close=None,
        active=True,
        disable_for=None,
        should_schedule=None,
    ):
        self.source = source
        self.probe = probe
        self.handoff = handoff
        self._active = active
        self._callbacks_installed = False
        self._close = close if close is not None else getattr(source, "close", None)
        self._current_thread_id = current_thread_id
        self._cursors = {}
        self._disable_for = disable_for
        self._lock = threading.RLock()
        self._on_switch = set_callback
        self._old_thread_switch_callback = None
        self._should_schedule = should_schedule
        self._thread_id = initial_thread_id

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def current_thread_id(self):
        return self._thread_id

    def set_disable_for(self, disable_for):
        self._disable_for = disable_for

    def set_replay_guards(self, *, should_schedule=None, should_start=None):
        self._should_schedule = should_schedule

    def set_on_switch(self, on_switch):
        self._on_switch = on_switch

    def activate(self):
        self._active = True
        self._install_thread_callbacks()

    def deactivate(self):
        self._active = False
        self._uninstall_thread_callbacks()
        self._close_handoff()

    def resume_thread(self):
        if not self._active:
            return None
        _debug_scheduler(f"replay resume current={self._read_current_thread_id()!r}")
        self._advance_scheduler()

    def advance_thread_schedule(
        self,
        *,
        allow_handoff=None,
        skip_handoff_if_current_done=False,
    ):
        if not self._active:
            return False
        _debug_scheduler(f"replay advance current={self._read_current_thread_id()!r}")
        if allow_handoff is None:
            allow_handoff = self._should_replay_thread_schedule()
        return self._advance_scheduled_switches(
            allow_handoff=allow_handoff,
            skip_handoff_if_current_done=skip_handoff_if_current_done,
        )

    def handoff_thread_schedule_to(self, thread_id):
        if not self._active:
            return False
        return self._handoff_next_switch_target(only_thread_id=thread_id)

    def _call_disabled(self, function, *args):
        if function is None:
            return None
        if self._disable_for is None:
            return function(*args)
        return self._disable_for(function, unwrap_args=False)(*args)

    def _should_replay_thread_schedule(self):
        if self._should_schedule is None:
            return True
        return self._should_schedule()

    def _thread_switch_callback(self, previous_delta, next_thread_id):
        schedule = self._should_replay_thread_schedule()
        _debug_scheduler(
            "replay switch callback "
            f"next={next_thread_id!r} schedule={schedule!r}"
        )
        if schedule:
            self._call_disabled(self._advance_observed_thread_switch, next_thread_id)
        if self._old_thread_switch_callback is not None:
            self._old_thread_switch_callback(previous_delta, next_thread_id)

    def _install_thread_callbacks(self):
        if self.probe is None or self._callbacks_installed:
            return
        self._old_thread_switch_callback = _get_thread_callback(self.probe, "switch")
        _set_thread_callback(
            self.probe,
            "switch",
            _retrace_callback(self.probe, self._thread_switch_callback),
        )
        self._callbacks_installed = True

    def _uninstall_thread_callbacks(self):
        if self.probe is None or not self._callbacks_installed:
            return
        _set_thread_callback(self.probe, "switch", self._old_thread_switch_callback)
        self._old_thread_switch_callback = None
        self._callbacks_installed = False

    def _set_thread_id(self, thread_id):
        if thread_id is not None:
            self._thread_id = thread_id

    def _read_current_thread_id(self):
        if self._current_thread_id is None:
            return None
        return self._current_thread_id()

    def cursor(self, thread_id=None):
        if thread_id is None:
            thread_id = self._thread_id
        return self._cursors.get(thread_id, ())

    def _cursor_after_delta(self, thread_id, delta):
        if delta is None:
            return None

        cursor = list(self._cursors.get(thread_id, ()))
        common = delta[0] if delta else 0
        del cursor[common:]
        cursor.extend(delta[1:])
        return tuple(cursor)

    def _handoff_to(self, thread_id):
        if self.handoff is None or thread_id is None:
            return False
        if not self._thread_exists(thread_id):
            return False
        current_thread_id = self._read_current_thread_id()
        if thread_id == current_thread_id:
            _debug_scheduler(f"replay skip handoff target={thread_id!r} current")
            return False
        _debug_scheduler(
            f"replay handoff from={current_thread_id!r} to={thread_id!r}"
        )
        self._call_disabled(self.handoff.to, thread_id)
        return True

    def _thread_exists(self, thread_id):
        if self.probe is None or not hasattr(self.probe, "coordinates"):
            return True
        try:
            self._call_disabled(self.probe.coordinates, thread_id)
        except (LookupError, ValueError):
            return False
        return True

    def _close_handoff(self):
        if self.handoff is None:
            return None
        return self._call_disabled(self.handoff.close)

    def _resume_thread_at_checkpoint(self):
        if not self._should_replay_thread_schedule():
            return None
        self._call_disabled(self.resume_thread)
        return None

    def _arm_thread_checkpoint(self, thread_id, cursor=_DEFAULT_CURSOR):
        if self.probe is None or thread_id is None:
            return False
        if cursor is _DEFAULT_CURSOR:
            cursor = self.cursor(thread_id)
        callback = _retrace_callback(self.probe, self._resume_thread_at_checkpoint)
        try:
            if cursor is None:
                _probe_call_at(self.probe, None, callback)
            else:
                _probe_call_at(self.probe, cursor, callback, callback)
        except (LookupError, ValueError):
            return False
        return True

    def _finish_thread_switch(self, message, previous_thread_id):
        self._arm_thread_checkpoint(previous_thread_id)
        self._call_disabled(self._on_switch, message)

    def _scheduled_previous_thread_id(self):
        previous_thread_id = self._thread_id
        if previous_thread_id is None:
            previous_thread_id = self._read_current_thread_id()
        return previous_thread_id

    def _arm_observed_switch_checkpoint(self, message):
        previous_thread_id = self._scheduled_previous_thread_id()
        if previous_thread_id is None:
            return
        cursor = self._cursor_after_delta(previous_thread_id, message.cursor_delta)
        self._arm_thread_checkpoint(previous_thread_id, cursor)

    def _advance_observed_thread_switch(self, next_thread_id):
        if not self._active:
            return False

        arm_message = None
        with self._lock:
            try:
                message = self.source.peek()
            except (LookupError, RuntimeError, StopIteration):
                return False
            if not isinstance(message, ThreadSwitchMessage):
                return False
            if message.thread_id != next_thread_id:
                _debug_scheduler(
                    "replay leave unmatched switch "
                    f"recorded={message.thread_id!r} observed={next_thread_id!r}"
                )
                arm_message = message

        if arm_message is not None:
            self._arm_observed_switch_checkpoint(arm_message)
            return False

        return self._advance_scheduler()

    def _consume_next_thread_switch(self, *, allow_handoff=True):
        if not self._active:
            return None

        handoff_target = None
        with self._lock:
            try:
                message = self.source.peek()
            except (LookupError, RuntimeError, StopIteration):
                return None
            if not isinstance(message, ThreadSwitchMessage):
                return None

            message = self.source.next()
            current_thread_id = self._read_current_thread_id()
            previous_thread_id = self._thread_id
            if previous_thread_id is None:
                previous_thread_id = current_thread_id
            _debug_scheduler(
                "replay consumed switch "
                f"previous={previous_thread_id!r} next={message.thread_id!r} "
                f"current={current_thread_id!r}"
            )
            if previous_thread_id is not None:
                self._cursors[previous_thread_id] = self._cursor_after_delta(
                    previous_thread_id,
                    message.cursor_delta,
                )
            self._set_thread_id(message.thread_id)
            if allow_handoff and message.thread_id != current_thread_id:
                handoff_target = message.thread_id

        return message, previous_thread_id, handoff_target

    def _advance_scheduler(self, *, allow_handoff=True):
        consumed = self._consume_next_thread_switch(allow_handoff=allow_handoff)
        if consumed is None:
            return False

        switch_message, previous_thread_id, handoff_target = consumed
        self._finish_thread_switch(switch_message, previous_thread_id)
        if handoff_target is not None:
            self._handoff_to(handoff_target)

        return True

    def _advance_scheduled_switches(
        self,
        *,
        allow_handoff=True,
        skip_handoff_if_current_done=False,
    ):
        consumed = None
        while True:
            if allow_handoff and self._handoff_next_switch_target(
                skip_handoff_if_current_done=skip_handoff_if_current_done,
            ):
                continue
            next_switch = self._consume_next_thread_switch(allow_handoff=False)
            if next_switch is None:
                break
            consumed = next_switch
            switch_message, previous_thread_id, _handoff_target = next_switch
            self._finish_thread_switch(switch_message, previous_thread_id)

        if consumed is None:
            return False

        return True

    def _has_future_switch_to_thread(self, thread_id, *, offset=0):
        if thread_id is None:
            return False
        while True:
            try:
                message = self.source.peek(offset)
            except (LookupError, RuntimeError, StopIteration):
                return False
            if (
                isinstance(message, ThreadSwitchMessage)
                and message.thread_id == thread_id
            ):
                return True
            offset += 1

    def _next_switch_is_adjacent(self):
        try:
            return isinstance(self.source.peek(1), ThreadSwitchMessage)
        except (LookupError, RuntimeError, StopIteration):
            return False

    def _handoff_next_switch_target(
        self,
        *,
        skip_handoff_if_current_done=False,
        only_thread_id=None,
    ):
        with self._lock:
            try:
                message = self.source.peek()
            except (LookupError, RuntimeError, StopIteration):
                return False
            if not isinstance(message, ThreadSwitchMessage):
                return False
            if message.cursor_delta is None:
                return False
            if self._next_switch_is_adjacent():
                return False

            current_thread_id = self._read_current_thread_id()
            if only_thread_id is not None and message.thread_id != only_thread_id:
                return False
            if message.thread_id == current_thread_id:
                return False
            if (
                skip_handoff_if_current_done
                and not self._has_future_switch_to_thread(current_thread_id, offset=1)
            ):
                return False
            if not self._thread_exists(message.thread_id):
                return False

        return self._handoff_to(message.thread_id)

    def next(self):
        allow_handoff = self._should_replay_thread_schedule()
        while True:
            self._advance_scheduled_switches(allow_handoff=allow_handoff)
            with self._lock:
                message = self.source.next()
            if not isinstance(message, (OnStartMessage, RunCompletedMessage)):
                return message

    read = next

    def close(self):
        self._close_handoff()
        if self._close is not None:
            return self._close()


class CallbackStream:
    __slots__ = (
        "source",
        "call_callback",
        "on_callback_result",
        "on_callback_error",
    )

    def __init__(
        self,
        source,
        call_callback,
        on_callback_result=None,
        on_callback_error=None,
    ):
        self.source = source
        self.call_callback = call_callback
        self.on_callback_result = on_callback_result
        self.on_callback_error = on_callback_error

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        while True:
            message = self.source.next()
            if isinstance(message, CallbackMessage):
                self.call_callback(message)
                continue
            if isinstance(message, CallbackResultMessage):
                if self.on_callback_result is not None:
                    self.on_callback_result(message)
                continue
            if isinstance(message, CallbackErrorMessage):
                if self.on_callback_error is not None:
                    self.on_callback_error(message)
                continue
            return message


__all__ = [
    "BindCloseMessage",
    "BindOpenMessage",
    "BindingStream",
    "CallMarkerMessage",
    "CallbackStream",
    "CallbackErrorMessage",
    "CallbackMessage",
    "CallbackResultMessage",
    "ExpectedBindMarker",
    "MessageStream",
    "OnStartMessage",
    "PeekableStream",
    "SchedulerStream",
    "SyncMessage",
    "ThreadSwitchMessage",
    "next_message",
]
