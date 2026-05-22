from __future__ import annotations

import functools
import _thread
import _signal
import operator
import gc
import weakref

import retrace

from retracesoftware import functional
from retracesoftware import stream
from retracesoftware import utils
from retracesoftware.gateway import GatewayPair
from retracesoftware.gateway._gatewaypair import _space_dispatch
from retracesoftware.proxy.contracts import (
    AsyncCapture,
    Binder,
    Checkpoint,
    ImmutableRegistry,
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
from retracesoftware.proxy.proxyfactory2 import ProxyFactory

from retracesoftware.proxy.typeextender import replay_shape_type
from retracesoftware.proxy.typepatcher import TypePatcher

def _binding_handle(binding):
    return binding.handle if hasattr(binding, "handle") else binding

class _RecordingBinder:
    def __init__(self, binder, writer) -> None:
        self.binder = binder
        self.writer = writer

    def _write_binding(self, value):
        binding = self.binder.lookup(value)
        self.writer.new_binding(_binding_handle(binding))

    def bind(self, value):
        self.binder.bind(value)
        self._write_binding(value)

    def autobind(self, value):
        self.binder.autobind(value)
        self._write_binding(value)

    def unbind(self, value):
        self.binder.unbind(value)

    def lookup(self, value):
        return self.binder.lookup(value)

    def __call__(self, value):
        return self.binder(value)

class _ReplayBinder:
    def __init__(self, bind_value) -> None:
        self.bind_value = bind_value
        self.bindings_by_id = {}

    def bind(self, value):
        binding = self.bind_value(value)
        self.bindings_by_id[id(value)] = binding

    autobind = bind

    def unbind(self, value):
        self.bindings_by_id.pop(id(value), None)

    def lookup(self, value):
        return self.bindings_by_id.get(id(value))

    def __call__(self, value):
        binding = self.lookup(value)
        if binding is None:
            return value
        return binding


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

class System2(Patcher, Binder, ImmutableRegistry):
    checkpoint: Checkpoint

    def wire_for_record(self, *, on_callback, on_error, on_result):
        self.gateway_pair.wire_for_record(
            is_passthrough = self.is_passthrough,
            on_callback = on_callback,
            on_error = on_error,
            on_result = on_result,
            int_proxy = self.proxy_factory.proxy_internal,
            ext_proxy = self.proxy_factory.proxy_external)

    def wire_for_replay(self, *, next_result):
        self.gateway_pair.wire_replay(
            self.proxy_factory.typefactory,
            is_passthrough=self.is_passthrough,
            next_result=next_result,
        )


    def __init__(
        self,
        *,
        binder,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> None:
        self.root_space = retrace.root_space
        self.gateway_pair = GatewayPair()
        self.internal_space = self.gateway_pair.sandbox_space
        self.external_space = self.gateway_pair._external_space
        self.immutable_types = {type(None)}
        immutable_types = self.immutable_types
        self.patched = utils.WeakSet()
        self.is_bound = utils.WeakSet()
        self.thread_counters = {}

        def bind_and_mark(value):
            self.is_bound.add(value)
            return binder.bind(value)

        self.bind = bind_and_mark
        self.binder = binder
        self.proxy_type_customizer = proxy_type_customizer
        self.original_to_retrace_type = {}
        self.retrace_to_original_type = {}
        self.extended_type_flags = {}

        self.is_immutable = utils.FastTypePredicate(lambda cls: cls in immutable_types).istypeof
        self.is_passthrough = functional.or_predicate(
            self.is_immutable,
            self.patched,
            self.is_bound,
            utils.is_wrapped,
            self.is_retrace_instance,
        )

        self.proxy_factory = ProxyFactory(
            binder=binder,
            gateway_pair=self.gateway_pair,
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

    def dispatch(self, *, internal, external, disabled):
        disp = _space_dispatch(disabled)
        disp[self.internal_space] = internal
        disp[self.external_space] = external
        return disp

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

    def next_thread_counter(self):
        thread_id = _thread.get_ident()
        counter = self.thread_counters.get(thread_id, 0)
        self.thread_counters[thread_id] = counter + 1
        return (thread_id, counter)

    def _register_retrace_type(self, original, retrace_type, *, kind, **flags):
        self.original_to_retrace_type[original] = retrace_type
        self.retrace_to_original_type[retrace_type] = original
        self.extended_type_flags[original] = {"kind": kind, **flags}
        return retrace_type

    def _existing_retrace_type(self, cls, *, kind, **flags):
        retrace_type = self.original_to_retrace_type.get(cls)
        if retrace_type is None:
            return None

        existing = self.extended_type_flags.get(cls, {})
        if existing.get("kind") != kind:
            raise ValueError(
                f"{cls!r} is already registered as a {existing.get('kind')} retrace type"
            )

        for name, value in flags.items():
            if existing.get(name) != value:
                raise ValueError(
                    f"{cls!r} is already registered with {name}={existing.get(name)!r}"
                )

        return retrace_type

    def retrace_type_for(self, cls):
        return self.original_to_retrace_type.get(cls)

    def original_type_for(self, cls):
        for base in getattr(cls, "__mro__", (cls,)):
            original = self.retrace_to_original_type.get(base)
            if original is not None:
                return original
        return None

    def is_retrace_type(self, cls):
        return self.original_type_for(cls) is not None

    def is_retrace_instance(self, obj):
        return self.is_retrace_type(type(obj))

    def extend_type(self, cls: type, *, python_distribution: bool) -> type:
        existing = self._existing_retrace_type(
            cls,
            kind="extended",
            python_distribution=python_distribution,
        )
        if existing is not None:
            return existing

        retrace_type = self._generate_extended_type(cls)
        return self._register_retrace_type(
            cls,
            retrace_type,
            kind="extended",
            python_distribution=python_distribution,
        )

    def _generate_extended_type(self, cls: type) -> type:
        return self.proxy_factory.typefactory.extended_type(cls)

    def wrap_type(self, cls: type) -> type:
        existing = self._existing_retrace_type(cls, kind="wrapped")
        if existing is not None:
            return existing

        retrace_type = self.proxy_factory.typefactory.instantiable_external_type(cls)
        return self._register_retrace_type(cls, retrace_type, kind="wrapped")

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

    def add_immutable_type(self, cls):
        self.immutable_types.add(cls)

    def add_immutable_types(self, *classes):
        self.immutable_types.update(classes)

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
        async_capture: AsyncCapture = AsyncCapture(),
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> System2:

        stream_binder = stream.Binder(
            on_delete=functional.sequence(_binding_handle, writer.binding_delete)
        )
        binder = _RecordingBinder(stream_binder, writer)

        encode_for_write = functional.walker(binder)

        system = System2(binder = binder, proxy_type_customizer = proxy_type_customizer)

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

        system.wire_for_record(
            on_callback = on_callback,
            on_error = on_error,
            on_result = functional.sequence(encode_for_write, writer.result),
        )

        if async_capture.thread_switch:
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

        if async_capture.signal:
            system_ref = weakref.ref(system)
            old_signal = _signal.signal
            wrapped_handlers = {}
            active = False

            def wrap_handler(handler):
                if not callable(handler):
                    return handler

                wrapped = wrapped_handlers.get(handler)
                if wrapped is not None:
                    return wrapped

                current_system = system_ref()
                if current_system is None:
                    return handler

                def record_signal(signum, frame):
                    nonlocal active
                    if active:
                        return handler(signum, frame)

                    current_system = system_ref()
                    if current_system is None:
                        return handler(signum, frame)

                    active = True
                    try:
                        writer.run_to_coordinate(
                            current_system.internal_space.thread_delta(),
                        )
                        writer.signal_callback(
                            encode_for_write(handler),
                            encode_for_write((signum, None)),
                            encode_for_write({}),
                        )
                        return handler(signum, frame)
                    finally:
                        active = False

                signal_dispatch = current_system.dispatch(
                    internal=record_signal,
                    external=handler,
                    disabled=handler,
                )

                @functools.wraps(handler)
                def signal_handler(signum, frame):
                    return signal_dispatch(signum, frame)

                wrapped_handlers[handler] = signal_handler
                return signal_handler

            def signal_signal(signum, handler):
                return old_signal(signum, wrap_handler(handler))

            def restore_signal():
                _signal.signal = old_signal

            _signal.signal = signal_signal
            weakref.finalize(system, restore_signal)

        if async_capture.gc:
            system_ref = weakref.ref(system)
            active = False

            def on_gc(phase, info):
                nonlocal active
                if active or phase != "start":
                    return

                current_system = system_ref()
                if current_system is None:
                    return

                active = True
                try:
                    writer.run_to_coordinate(
                        current_system.internal_space.thread_delta()
                    )
                    writer.gc_collect(info.get("generation", 2))
                finally:
                    active = False

            def remove_gc_callback():
                try:
                    gc.callbacks.remove(on_gc)
                except ValueError:
                    pass

            gc.callbacks.append(on_gc)
            weakref.finalize(system, remove_gc_callback)

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
            return stream.Binding(message.handle)

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

        replay_binder = _ReplayBinder(bind)
        system = System2(
            binder=replay_binder,
            proxy_type_customizer=proxy_type_customizer,
        )
        system.wire_for_replay(next_result=next_result)

        def replay_extend_type(cls: type, *, python_distribution: bool) -> type:
            existing = system._existing_retrace_type(
                cls,
                kind="extended",
                python_distribution=python_distribution,
            )
            if existing is not None:
                return existing

            source_type = cls if python_distribution else replay_shape_type(cls)
            retrace_type = system._generate_extended_type(source_type)
            return system._register_retrace_type(
                cls,
                retrace_type,
                kind="extended",
                python_distribution=python_distribution,
            )

        system.extend_type = replay_extend_type

        system.checkpoint = system.root_space.wrap(checkpoint)
        system.handoff = handoff

        if debug:
            on_call = functional.sequence(
                functional.positional_param(0),
                system.checkpoint)

            system.set_on_call(on_call)

        return system
