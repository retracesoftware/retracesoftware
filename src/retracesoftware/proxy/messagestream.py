from collections import deque

import retracesoftware.stream as stream

from retracesoftware.protocol.messages import (
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    ProtocolMessage,
    ResultMessage,
    StacktraceMessage,
)
from retracesoftware.install.monitoring import (
    begin_suppress_monitoring,
    end_suppress_monitoring,
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
            setattr(callbacks, _probe_callback_name(kind), callback)
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
    disable = getattr(probe, "disable", None) or getattr(probe, "exclude", None)
    if disable is None:
        return callback
    return disable(callback)


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


class OnStartMessage(ProtocolMessage):
    __slots__ = ()


class CallbackMessage(CallMessage):
    __slots__ = ()


class CallbackResultMessage(ResultMessage):
    __slots__ = ()


class CallbackErrorMessage(ErrorMessage):
    __slots__ = ()


class ThreadStartMessage(ProtocolMessage):
    __slots__ = ()


class ThreadYieldMessage(ProtocolMessage):
    __slots__ = ("cursor_delta",)

    def __init__(self, cursor_delta, *, thread_id=None):
        super().__init__(thread_id=thread_id)
        self.cursor_delta = cursor_delta


class ThreadResumeMessage(ProtocolMessage):
    __slots__ = ()


class BindOpenMessage(ProtocolMessage):
    __slots__ = ("handle",)

    def __init__(self, handle):
        super().__init__()
        self.handle = handle


class BindCloseMessage(ProtocolMessage):
    __slots__ = ("handle",)

    def __init__(self, handle):
        super().__init__()
        self.handle = handle


class MessageStream:
    __slots__ = ("source", "_close")

    def __init__(self, source, *, close=None):
        self.source = source
        self._close = close if close is not None else getattr(source, "close", None)

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        return next_message(self.source)

    read = next

    def close(self):
        if self._close is not None:
            return self._close()


def _binding_handle(binding):
    return binding.handle if hasattr(binding, "handle") else binding


def _is_proxy_ref(value):
    return (
        type(value).__name__ == "ProxyRef"
        and type(value).__module__ == "retracesoftware.proxy.system"
    )


def _resolve_value(value, bindings):
    if isinstance(value, stream.Binding):
        return _resolve_value(bindings[value.handle], bindings)
    if _is_proxy_ref(value):
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
            _resolve_value(message.value, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, StacktraceMessage):
        return type(message)(
            _resolve_value(message.stacktrace, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, ThreadYieldMessage):
        return type(message)(
            _resolve_value(message.cursor_delta, bindings),
            thread_id=thread_id,
        )
    if isinstance(message, (ThreadStartMessage, ThreadResumeMessage, OnStartMessage)):
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

    def peek(self):
        shadow_bindings = dict(self._bindings)
        offset = 0

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
        "set_callback",
        "probe",
        "handoff",
        "_active",
        "_callbacks_installed",
        "_close",
        "_current_thread_id",
        "_cursors",
        "_disable_for",
        "_on_yield",
        "_old_thread_resume_callback",
        "_old_thread_start_callback",
        "_pending_starts",
        "_parked_starts",
        "_should_schedule",
        "_should_start",
        "_skip_until_thread_id",
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
        should_start=None,
    ):
        self.source = source
        self.set_callback = set_callback
        self.probe = probe
        self.handoff = handoff
        self._active = active
        self._callbacks_installed = False
        self._close = close if close is not None else getattr(source, "close", None)
        self._current_thread_id = current_thread_id
        self._cursors = {}
        self._disable_for = disable_for
        self._on_yield = None
        self._old_thread_resume_callback = None
        self._old_thread_start_callback = None
        self._pending_starts = set()
        self._parked_starts = set()
        self._should_schedule = should_schedule
        self._should_start = should_start
        self._skip_until_thread_id = None
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
        self._should_start = should_start

    def set_on_yield(self, on_yield):
        self._on_yield = on_yield

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
        while self._advance_scheduler():
            pass

    def start_thread(self):
        if not self._active:
            return None

        current_thread_id = self._read_current_thread_id()
        if current_thread_id in self._pending_starts:
            self._pending_starts.remove(current_thread_id)
            self._set_thread_id(current_thread_id)
            return None

        message = self.source.peek()
        if (
            isinstance(message, ThreadStartMessage)
            and message.thread_id == current_thread_id
        ):
            self.source.next()
            self._set_thread_id(message.thread_id)
            return None

        if isinstance(message, ThreadStartMessage):
            if self.handoff is not None and current_thread_id is not None:
                self._parked_starts.add(current_thread_id)
                self._handoff_start()
                self._parked_starts.discard(current_thread_id)
                if current_thread_id in self._pending_starts:
                    self._pending_starts.remove(current_thread_id)
                    self._set_thread_id(current_thread_id)
            return None
        return None

    enter_thread = start_thread

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

    def _should_replay_thread_start(self):
        if self._should_start is None:
            return True
        return self._should_start()

    def _thread_start_callback(self):
        if self._should_replay_thread_start():
            self._call_disabled(self.start_thread)
        if self._old_thread_start_callback is not None:
            self._old_thread_start_callback()

    def _thread_resume_callback(self):
        if self._should_replay_thread_schedule():
            self._call_disabled(self.resume_thread)
        if self._old_thread_resume_callback is not None:
            self._old_thread_resume_callback()

    def _install_thread_callbacks(self):
        if self.probe is None or self._callbacks_installed:
            return
        self._old_thread_start_callback = _get_thread_callback(self.probe, "start")
        self._old_thread_resume_callback = _get_thread_callback(self.probe, "resume")
        _set_thread_callback(
            self.probe,
            "start",
            _retrace_callback(self.probe, self._thread_start_callback),
        )
        _set_thread_callback(
            self.probe,
            "resume",
            _retrace_callback(self.probe, self._thread_resume_callback),
        )
        self._callbacks_installed = True

    def _uninstall_thread_callbacks(self):
        if self.probe is None or not self._callbacks_installed:
            return
        _set_thread_callback(self.probe, "start", self._old_thread_start_callback)
        _set_thread_callback(self.probe, "resume", self._old_thread_resume_callback)
        self._old_thread_start_callback = None
        self._old_thread_resume_callback = None
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
        cursor = list(self._cursors.get(thread_id, ()))
        common = delta[0] if delta else 0
        del cursor[common:]
        cursor.extend(delta[1:])
        return tuple(cursor)

    def _handoff_to(self, thread_id):
        if self.handoff is None or thread_id is None:
            return None
        if thread_id == self._read_current_thread_id():
            return None
        return self._call_disabled(self.handoff.to, thread_id)

    def _handoff_start(self):
        if self.handoff is None:
            return None
        return self._call_disabled(self.handoff.start)

    def _resume_is_replayable(self, thread_id):
        return (
            thread_id in self._cursors
            or thread_id in self._pending_starts
            or thread_id in self._parked_starts
        )

    def _has_resume_for_thread_ahead(self, thread_id):
        if thread_id is None:
            return False

        offset = 0
        while True:
            try:
                message = self.source.peek(offset)
            except TypeError:
                return False
            except StopIteration:
                return False
            if (
                isinstance(message, ThreadResumeMessage)
                and message.thread_id == thread_id
            ):
                return True
            offset += 1

    def _start_skipping_until_thread(self, current_thread_id):
        if current_thread_id is None:
            return False
        if not self._has_resume_for_thread_ahead(current_thread_id):
            return False
        self._skip_until_thread_id = current_thread_id
        return True

    def _advance_skipped_thread_segment(self):
        thread_id = self._skip_until_thread_id
        if thread_id is None:
            return False

        message = self.source.peek()
        if isinstance(message, BindCloseMessage):
            return False

        if (
            isinstance(message, ThreadResumeMessage)
            and message.thread_id == thread_id
        ):
            message = self.source.next()
            self._set_thread_id(message.thread_id)
            self._skip_until_thread_id = None
            return True

        self.source.next()
        return True

    def _skip_unreplayed_thread_segment(self, current_thread_id):
        if not self._start_skipping_until_thread(current_thread_id):
            return

        while True:
            if not self._advance_skipped_thread_segment():
                return

    def _close_handoff(self):
        if self.handoff is None:
            return None
        return self._call_disabled(self.handoff.close)

    def _resume_thread_at_checkpoint(self):
        self._call_disabled(self.resume_thread)

    def _arm_thread_checkpoint(self, message):
        if self.probe is None:
            return
        callback = _retrace_callback(self.probe, self._resume_thread_at_checkpoint)
        try:
            _probe_call_at(
                self.probe,
                message.thread_id,
                self.cursor(message.thread_id),
                callback,
                callback,
            )
        except (LookupError, ValueError):
            return

    def _advance_scheduler(self, *, allow_start_handoff=False):
        if not self._active:
            return False

        if self._advance_skipped_thread_segment():
            return True

        message = self.source.peek()
        if isinstance(message, ThreadStartMessage):
            current_thread_id = self._read_current_thread_id()
            if message.thread_id != current_thread_id:
                if not allow_start_handoff:
                    return False
                message = self.source.next()
                self._set_thread_id(message.thread_id)
                self._pending_starts.add(message.thread_id)
                if message.thread_id in self._parked_starts:
                    self._handoff_to(message.thread_id)
                return True
            message = self.source.next()
            self._set_thread_id(message.thread_id)
            return True

        if isinstance(message, ThreadYieldMessage):
            message = self.source.next()
            if message.thread_id is None:
                message.thread_id = self._thread_id
            else:
                self._set_thread_id(message.thread_id)
            self._cursors[message.thread_id] = self._cursor_after_delta(
                message.thread_id,
                message.cursor_delta,
            )
            self._arm_thread_checkpoint(message)
            self._call_disabled(self.set_callback, message)
            self._call_disabled(self._on_yield)
            return True

        if isinstance(message, ThreadResumeMessage):
            message = self.source.next()
            current_thread_id = self._read_current_thread_id()
            self._set_thread_id(message.thread_id)
            if self._resume_is_replayable(message.thread_id):
                self._handoff_to(message.thread_id)
            elif (
                message.thread_id is not None
                and message.thread_id != current_thread_id
            ):
                self._skip_unreplayed_thread_segment(current_thread_id)
            return True

        return False

    def next(self):
        while self._advance_scheduler(allow_start_handoff=True):
            pass
        return self.source.next()

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


def next_message(tape):
    message_type = _read(tape)

    if stream._is_bind_open(message_type):
        return BindOpenMessage(stream._bind_index(message_type))
    elif stream._is_bind_close(message_type):
        return BindCloseMessage(stream._bind_index(message_type))
    elif message_type == 'ON_START':
        return OnStartMessage()
    elif message_type == 'RESULT':
        return ResultMessage(_read(tape))
    elif message_type == 'ERROR':
        return ErrorMessage(_read(tape))
    elif message_type == 'CALLBACK':
        return CallbackMessage(_read(tape), _read(tape), _read(tape))
    elif message_type == 'CALLBACK_RESULT':
        return CallbackResultMessage(_read(tape))
    elif message_type == 'CALLBACK_ERROR':
        return CallbackErrorMessage(_read(tape))
    elif message_type == 'CHECKPOINT':
        return CheckpointMessage(_read(tape))
    elif message_type == 'STACKTRACE':
        return StacktraceMessage(_read(tape))
    elif message_type == 'THREAD_START':
        return ThreadStartMessage(thread_id=_read(tape))
    elif message_type == 'THREAD_YIELD':
        return ThreadYieldMessage(_read(tape))
    elif message_type == 'THREAD_RESUME':
        return ThreadResumeMessage(thread_id=_read(tape))
    elif message_type == 'THREAD_SWITCH':
        return ThreadResumeMessage(thread_id=_read(tape))
    elif message_type == 'NEW_BINDING':
        return BindOpenMessage(_binding_handle(_read(tape)))
    elif message_type == 'BINDING_DELETE':
        return BindCloseMessage(_binding_handle(_read(tape)))
    else:
        return message_type


__all__ = [
    "BindCloseMessage",
    "BindOpenMessage",
    "BindingStream",
    "CallbackStream",
    "CallbackErrorMessage",
    "CallbackMessage",
    "CallbackResultMessage",
    "ExpectedBindMarker",
    "MessageStream",
    "OnStartMessage",
    "PeekableStream",
    "SchedulerStream",
    "ThreadResumeMessage",
    "ThreadStartMessage",
    "ThreadYieldMessage",
    "next_message",
]
