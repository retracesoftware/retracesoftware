from __future__ import annotations

import functools
import _thread
import _signal
import operator
import gc

import retrace

from retracesoftware import functional
from retracesoftware import stream
from retracesoftware import utils
from retracesoftware.gateway import GatewayPair
from retracesoftware.gateway._gatewaypair import _space_dispatch
from retracesoftware.proxy.contracts import (
    Checkpoint,
    Patcher,
    ProxyTypeCustomizer,
    TraceReader,
    TraceWriter,
)
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    BindOpenMessage,
    CallbackMessage,
    CheckpointMessage,
    ErrorMessage,
    GCMessage,
    ResultMessage,
    RunToCoordinateMessage,
    SignalMessage,
    SwitchThreadMessage,
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

class System2(Patcher):
    checkpoint: Checkpoint

    def __init__(
        self,
        *,
        create_gateway_pair,
        bind,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> None:
        self.root_space = retrace.root_space
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
            proxy_type_customizer=proxy_type_customizer,
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

    def update_external(self, f):
        self.gateway_pair.external = f(self.gateway_pair.external)

    def set_on_call(self, on_call):
        self.update_external(lambda f: utils.observer(
            function = f,
            on_call = on_call))

    @staticmethod
    def record_system(
        *,
        writer: TraceWriter,
        debug : bool = False,
        capture_gc: bool = False,
        capture_signals: bool = False,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> System2:
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

        def on_signal_callback(fn, *args, **kwargs):
            writer.run_to_coordinate(
                system.internal_space.thread_delta(),
            )
            writer.signal_callback(
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

        system = System2(
            create_gateway_pair=create_gateway_pair,
            bind=bind,
            proxy_type_customizer=proxy_type_customizer,
        )

        system.internal_space.thread_switch = writer.thread_switch

        system.checkpoint = system.root_space.wrap(functional.spread(
            writer.checkpoint,
            functional.repeatedly(system.internal_space.thread_delta),
            functional.repeatedly(_thread.get_ident),
            encode_for_write))

        if debug:
            on_call = functional.sequence(
                functional.positional_param(0),
                system.checkpoint)

            system.set_on_call(on_call)

        if capture_signals:
            old_signal = None
            wrapped_handlers = {}
            active = False

            def wrap_handler(handler):
                if not callable(handler):
                    return handler

                wrapped = wrapped_handlers.get(handler)
                if wrapped is not None:
                    return wrapped

                @functools.wraps(handler)
                def signal_handler(signum, frame):
                    nonlocal active
                    if active:
                        return handler(signum, frame)

                    active = True
                    try:
                        on_signal_callback(handler, signum, None)
                        return handler(signum, frame)
                    finally:
                        active = False

                wrapped_handlers[handler] = signal_handler
                return signal_handler

            def signal_signal(signum, handler):
                return old_signal(signum, wrap_handler(handler))

            def install_signal_capture():
                nonlocal old_signal
                old_signal = _signal.signal
                _signal.signal = signal_signal

            def uninstall_signal_capture():
                _signal.signal = old_signal

            system.on_start = utils.runall(system.on_start, install_signal_capture)
            system.on_end = utils.runall(uninstall_signal_capture, system.on_end)

        if capture_gc:
            active = False

            def on_gc(phase, info):
                nonlocal active
                if active or phase != "start":
                    return

                active = True
                try:
                    writer.run_to_coordinate(system.internal_space.thread_delta())
                    writer.gc_collect(info.get("generation", 2))
                finally:
                    active = False

            def install_gc_capture():
                gc.callbacks.append(on_gc)

            def uninstall_gc_capture():
                try:
                    gc.callbacks.remove(on_gc)
                except ValueError:
                    pass

            system.on_start = utils.runall(system.on_start, install_gc_capture)
            system.on_end = utils.runall(uninstall_gc_capture, system.on_end)

        return system

    @staticmethod
    def replay_system(
        *,
        reader: TraceReader,
        debug : bool = False,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> System2:
        reader = _PeekableReader(reader)
        handoff = retrace.ThreadHandoff()
        bindings = {}
        cursors = _ThreadCursors()
        system = None

        resolve = functional.walker(functional.if_then_else(
            functional.isinstanceof(stream.Binding),
            functional.sequence(operator.attrgetter("handle"), bindings.__getitem__),
            functional.identity,
        ))
            
        def run_callback(message):
            try:
                system.internal_space.apply(
                    functional.call,
                    resolve(message.fn),
                    resolve(message.args),
                    resolve(message.kwargs),
                )
            except Exception:
                pass

        def advance_to_coordinate(result=None):
            while isinstance(reader.peek(), RunToCoordinateMessage):
                message = reader()
                thread_id = _thread.get_ident()
                cursor = cursors.advance(thread_id, message.cursor_delta)
                instruction = reader.peek()

                if isinstance(instruction, SwitchThreadMessage):
                    instruction = reader()
                    on_hit = functional.repeatedly(handoff.to, instruction.thread_id)
                    error = ReplayThreadScheduleError(
                        f"overshot replay thread switch to {instruction.thread_id!r}"
                    )
                elif isinstance(instruction, SignalMessage):
                    instruction = reader()
                    on_hit = functional.repeatedly(run_callback, instruction)
                    error = ReplayThreadScheduleError("overshot replay signal")
                elif isinstance(instruction, GCMessage):
                    instruction = reader()
                    on_hit = functional.repeatedly(gc.collect, instruction.generation)
                    error = ReplayThreadScheduleError("overshot replay gc")
                elif isinstance(instruction, CallbackMessage):
                    instruction = reader()
                    on_hit = functional.repeatedly(run_callback, instruction)
                    error = ReplayThreadScheduleError("overshot replay callback")
                else:
                    on_hit = utils.noop
                    error = ReplayThreadScheduleError(
                        f"overshot replay coordinate {cursor!r}"
                    )

                system.internal_space.call_at(
                    thread_id,
                    cursor,
                    on_hit,
                    functional.repeatedly(_raise, error),
                )
            return result

        def read_message():
            while True:
                advance_to_coordinate()
                message = reader()
                if isinstance(message, BindCloseMessage):
                    bindings.pop(message.handle, None)
                    continue
                elif isinstance(message, SwitchThreadMessage):
                    raise RuntimeError(
                        f"switch thread without run-to-coordinate: {message!r}"
                    )
                elif isinstance(message, SignalMessage):
                    run_callback(message)
                    continue
                elif isinstance(message, GCMessage):
                    gc.collect(message.generation)
                    continue
                elif isinstance(message, CallbackMessage):
                    run_callback(message)
                    continue

                return message

        def bind(value):
            message = read_message()
            if not isinstance(message, BindOpenMessage):
                raise RuntimeError(f"expected replay binding, got {message!r}")
            bindings[message.handle] = value

        def checkpoint_thread(thread_id):
            current = _thread.get_ident()
            if thread_id != current:
                from retracesoftware.install import ReplayDivergence
                raise ReplayDivergence(
                    "checkpoint thread difference: "
                    f"{thread_id!r} was expecting {current!r}"
                )

        def checkpoint_cursor(delta):
            cursor = cursors.advance(_thread.get_ident(), delta)
            current = system.internal_space.coordinates()
            if cursor != current:
                from retracesoftware.install import ReplayDivergence
                raise ReplayDivergence(
                    "checkpoint cursor difference: "
                    f"{cursor!r} was expecting {current!r}"
                )

        def checkpoint(value):
            message = read_message()
            if not isinstance(message, CheckpointMessage):
                raise RuntimeError(f"expected replay checkpoint, got {message!r}")

            checkpoint_thread(message.thread_id)
            checkpoint_cursor(message.cursor_delta)

            record_value = resolve(message.value)
            if record_value != value:
                from retracesoftware.install import ReplayDivergence
                raise ReplayDivergence(
                    f"checkpoint difference: {record_value!r} was expecting {value!r}"
                )

        @utils.striptraceback
        def next_result(*_args, **_kwargs):
            
            message = read_message()
    
            if isinstance(message, ResultMessage):
                return resolve(message.result)
                
            if isinstance(message, ErrorMessage):
                raise resolve(message.error)
                
            raise RuntimeError(f"expected replay result or error, got {message!r}")

        create_gateway_pair = functools.partial(
            GatewayPair.create_replaying_pair,
            next_result=next_result,
        )

        system = System2(
            create_gateway_pair=create_gateway_pair,
            bind=bind,
            proxy_type_customizer=proxy_type_customizer,
        )

        system.checkpoint = system.root_space.wrap(checkpoint)
        system.handoff = handoff

        if debug:
            on_call = functional.sequence(
                functional.positional_param(0),
                system.checkpoint)

            system.set_on_call(on_call)

        return system
