from __future__ import annotations

import functools
import _thread

import retrace

from retracesoftware import functional
from retracesoftware import stream
from retracesoftware import utils
from retracesoftware.gateway import GatewayPair
from retracesoftware.gateway._gatewaypair import _space_dispatch
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    ErrorMessage,
    ResultMessage,
    TraceReader,
    TraceWriter,
    ThreadSwitchMessage,
)
from retracesoftware.proxy.typepatcher import TypePatcher

def _binding_handle(binding):
    return binding.handle if hasattr(binding, "handle") else binding

class _PeekableReader:
    def __init__(self, reader: TraceReader) -> None:
        self.reader = reader
        self.buffer = []

    def peek(self):
        if not self.buffer:
            self.buffer.append(self.reader())
        return self.buffer[0]

    def __call__(self):
        if self.buffer:
            return self.buffer.pop(0)
        return self.reader()

class _ThreadCursors:
    def __init__(self) -> None:
        self.cursors = {}

    def advance(self, thread_id, delta):
        cursor = list(self.cursors.get(thread_id, ()))
        common = delta[0] if delta else 0
        del cursor[common:]
        cursor.extend(delta[1:])
        cursor = tuple(cursor)
        self.cursors[thread_id] = cursor
        return cursor

class ReplayThreadScheduleError(BaseException):
    pass

def _raise(error):
    raise error

class System2:
    def __init__(self, create_gateway_pair, bind) -> None:
        self.internal_space = retrace.CoordinateSpace()
        self.external_space = retrace.CoordinateSpace()
        self.immutable_types = set()
        self.patched = utils.WeakSet()
        self.is_bound = utils.WeakSet()
        self.bind = utils.runall(self.is_bound.add, bind)

        self.is_immutable = utils.FastTypePredicate(lambda cls: cls in self.immutable_types).istypeof
        self.is_passthrough = functional.or_predicate(
            self.is_immutable,
            self.patched,
            self.is_bound,
            utils.is_wrapped,
        )

        self.gateway_pair = create_gateway_pair(
            internal_space=self.internal_space,
            external_space=self.external_space,
            is_passthrough=self.is_passthrough,
            bind=self.bind,
        )

        on_alloc = utils.runall(self.bind, self.patched.add)

        self.type_patcher = TypePatcher(
            self.gateway_pair,
            bind=self.bind,
            on_alloc = self.dispatch(
                internal = on_alloc,
                external = on_alloc,
                disabled = utils.noop))

        self.patched_types = self.type_patcher.patched_types
        self.on_start = utils.noop
        self.on_end = utils.noop

    def dispatch(self, *, internal, external, disabled):
        disp = _space_dispatch(disabled)
        disp[self.internal_space] = internal
        disp[self.external_space] = external
        return disp

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

    def patch_function(self, fn):
        if not self.is_bound(fn):
            self.bind(fn)
        wrapped = self._wrapped_function(self.gateway_pair.external, fn)
        patched = utils.wrapped_callable(wrapped)
        self.bind(patched)
        return patched

    def patch_type(self, cls):
        self.type_patcher.patch_type(cls)
        return functools.partial(self.type_patcher.unpatch_type, cls)

    @staticmethod
    def record_system(writer: TraceWriter) -> System2:
        binder = stream.Binder(
            on_delete=functional.sequence(_binding_handle, writer.binding_delete)
        )

        bind = functional.sequence(binder.bind, _binding_handle, writer.new_binding)
        encode_for_write = functional.walker(binder)

        def on_callback(fn, *args, **kwargs):
            writer.callback(
                encode_for_write(fn),
                encode_for_write(args),
                encode_for_write(kwargs),
            )

        on_error = functional.sequence(
            functional.positional_param(1),
            functional.if_then_else(
                functional.isinstanceof(Exception),
                writer.error,
                utils.noop))

        create_gateway_pair = functools.partial(
            GatewayPair.create_recording_pair,
            on_callback=on_callback,
            on_error=on_error,
            on_result=functional.sequence(encode_for_write, writer.result),
        )

        system = System2(create_gateway_pair=create_gateway_pair, bind=bind)

        system.internal_space.thread_switch = writer.thread_switch

        return system

    @staticmethod
    def replay_system(reader : TraceReader) -> System2:
        reader = _PeekableReader(reader)
        handoff = retrace.ThreadHandoff()
        bindings = {}
        cursors = _ThreadCursors()
        system = None

        def resolve(value):
            if isinstance(value, stream.Binding):
                return resolve(bindings[value.handle])
            if isinstance(value, tuple):
                return tuple(resolve(item) for item in value)
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, dict):
                return {
                    resolve(key): resolve(item)
                    for key, item in value.items()
                }
            return value

        def advance_thread_switches():
            while isinstance(reader.peek(), ThreadSwitchMessage):
                message = reader()

                current_thread_id = _thread.get_ident()
                cursor = cursors.advance(current_thread_id, message.cursor_delta)

                system.internal_space.call_at(
                    current_thread_id,
                    cursor,
                    functional.repeatedly(handoff.to, message.thread_id),
                    functional.repeatedly(
                        _raise,
                        ReplayThreadScheduleError(
                            f"overshot replay thread switch to {message.thread_id!r}"
                        ),
                    ),
                )

        def read_message():
            while True:
                advance_thread_switches()
                message = reader()
                if isinstance(message, BindCloseMessage):
                    bindings.pop(message.handle, None)
                    continue
                return message

        def bind(value):
            message = read_message()
            if not isinstance(message, BindOpenMessage):
                raise RuntimeError(f"expected replay binding, got {message!r}")
            bindings[message.handle] = value

        def next_result(*_args, **_kwargs):
            message = read_message()
            if isinstance(message, BindOpenMessage):
                raise RuntimeError("bind marker returned when bind was expected")
            if isinstance(message, ResultMessage):
                return resolve(message.result)
            if isinstance(message, ErrorMessage):
                raise resolve(message.error)
            raise RuntimeError(f"expected replay result or error, got {message!r}")

        create_gateway_pair = functools.partial(
            GatewayPair.create_replaying_pair,
            next_result=next_result,
        )

        system = System2(create_gateway_pair=create_gateway_pair, bind=bind)
        system.handoff = handoff
        return system
