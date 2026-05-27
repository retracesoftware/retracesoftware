from __future__ import annotations

import functools
import _thread
import _signal
import operator
import gc
import weakref
import sys
from contextlib import contextmanager
from typing import NamedTuple, Callable, Any

import retrace

from retracesoftware import functional
from retracesoftware import stream
from retracesoftware import utils
from retracesoftware.gateway import GatewayPair
from retracesoftware.gateway._gatewaypair import _space_apply, _space_dispatch, fallback
from retracesoftware.install.monitoring import (
    begin_suppress_monitoring,
    end_suppress_monitoring,
)
from retracesoftware.proxy.contracts import (
    AsyncCapture,
    Binder,
    Checkpoint,
    ImmutableRegistry,
    ProxyRuntime,
    ProxyTypeCustomizer,
    TraceReader,
    TraceWriter,
)
from retracesoftware.proxy.traceio import (
    BindCloseMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    ErrorMessage,
    GCMessage,
    OnStartMessage,
    ResultMessage,
    RunCompletedMessage,
    RunToCoordinateMessage,
    SignalMessage,
    SwitchThreadMessage,
)
from retracesoftware.proxy.proxyfactory2 import ProxyFactory

from retracesoftware.proxy.typeextender import ExtendedType, replay_shape_type

wrapped_callable = utils.wrapped_callable


class disabled_callable(wrapped_callable):
    __slots__ = ("_retrace_call",)

    def __new__(cls, wrapped, call):
        return super().__new__(cls, wrapped)

    def __init__(self, wrapped, call):
        self._retrace_call = call

    def __call__(self, *args, **kwargs):
        return self._retrace_call(*args, **kwargs)


class LifecycleHooks(NamedTuple):
    on_start: Callable[..., Any] | None = None
    on_end: Callable[..., Any] | None = None


def _binding_handle(binding):
    if isinstance(binding, stream.Binding):
        return stream.Binding((0, int(binding.index)))
    return binding.handle if hasattr(binding, "handle") else binding

def _phase_internal():
    return "internal"

def _phase_external():
    return "external"

def _phase_disabled():
    return None

def _unwired_record_replay_operation(_recorder, _replayer):
    raise RuntimeError(
        "record_replay_operation requires an active record or replay system"
    )

class _RecordReplayOperationWriter(NamedTuple):
    result: Callable[[Any], Any]

class _RecordReplayOperationReader(NamedTuple):
    result: Callable[..., Any]

class _ReplayBinder:
    def __init__(
        self,
        binder,
        *,
        on_bind=utils.noop,
        on_unbind=utils.noop,
    ) -> None:
        self.binder = binder
        self.on_bind = on_bind
        self.on_unbind = on_unbind

    def bind(self, value):
        self.binder.bind(value)
        binding = self.binder.lookup(value)
        self.on_bind(value, binding)

    def autobind(self, value):
        self.binder.autobind(value)
        binding = self.binder.lookup(value)
        self.on_bind(value, binding)

    def unbind(self, value):
        binding = self.binder.lookup(value)
        self.binder.unbind(value)
        if binding is not None:
            self.on_unbind(value, binding)

    def lookup(self, value):
        return self.binder.lookup(value)

    def __call__(self, value):
        return self.binder(value)

class _MarkingBinder:
    def __init__(self, binder, mark) -> None:
        self.binder = binder
        self.mark = mark

    def bind(self, value):
        self.mark(value)
        return self.binder.bind(value)

    def autobind(self, value):
        self.mark(value)
        return self.binder.autobind(value)

    def unbind(self, value):
        return self.binder.unbind(value)

    def lookup(self, value):
        return self.binder.lookup(value)

    def __call__(self, value):
        return self.binder(value)

class _PeekableReader:
    def __init__(self, reader: TraceReader) -> None:
        self.reader = reader
        self.buffer = []

    def peek(self, offset=0):
        while len(self.buffer) <= offset:
            self.buffer.append(self.reader())
        return self.buffer[offset]

    def __call__(self):
        if self.buffer:
            return self.buffer.pop(0)
        return self.reader()

class _ThreadCursors:
    def __init__(self) -> None:
        self.cursors = {}

    def after(self, thread_id, delta):
        if delta is None:
            return None

        cursor = list(self.cursors.get(thread_id, ()))
        common = delta[0] if delta else 0
        del cursor[common:]
        cursor.extend(delta[1:])
        return tuple(cursor)

    def advance(self, thread_id, delta):
        if delta is None:
            self.cursors[thread_id] = None
            return None

        cursor = self.after(thread_id, delta)
        self.cursors[thread_id] = cursor
        return cursor

class ReplayThreadScheduleError(BaseException):
    pass

def _raise(error):
    raise error

class System(ProxyRuntime, Binder, ImmutableRegistry):
    checkpoint: Checkpoint

    def wire_for_record(
        self,
        *,
        on_callback,
        on_error,
        on_result,
        wrap_external_call=functional.identity,
    ):
        system_ref = weakref.ref(self)

        def original_type_for(cls):
            current_system = system_ref()
            if current_system is None:
                return None
            return current_system.original_type_for(cls)

        def dynamic_proxy_target(cls):
            return getattr(cls, "__retrace_target_class__", None)

        unwrap_type = functional.firstof(
            original_type_for,
            dynamic_proxy_target,
            functional.identity,
        )
        unwrap = functional.if_then_else(
            functional.isinstanceof(type),
            unwrap_type,
            utils.try_unwrap
        )

        self.gateway_pair.wire_for_record(
            is_passthrough = self.is_passthrough,
            on_callback = on_callback,
            on_error = on_error,
            on_result = on_result,
            int_proxy = self.proxy_factory.proxy_internal,
            ext_proxy = self.proxy_factory.proxy_external,
            unwrap = unwrap,
            wrap_external_call = wrap_external_call)

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
        on_del: Callable[[Any], Any] | None = None,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> None:
        self.root_space = retrace.root_space
        self.disabled_space = getattr(retrace, "disabled_space", retrace.CoordinateSpace())
        self.gateway_pair = GatewayPair()
        self.internal_space = self.gateway_pair.sandbox_space
        self.external_space = self.gateway_pair._external_space
        self.int_gateway = self.gateway_pair.internal
        self.ext_gateway = self.gateway_pair.external
        self.immutable_types = {type(None)}
        self.is_bound = utils.WeakSet()
        self.lifecycle_hooks = LifecycleHooks()
        self.retrace_mode = None
        self._record_replay_operation = _unwired_record_replay_operation
        self._is_immutable_predicate = utils.noop
        self._is_passthrough_predicate = utils.noop
        self_ref = weakref.ref(self)

        def is_immutable(value):
            current_system = self_ref()
            if current_system is None:
                return False
            return current_system._is_immutable_predicate(value)

        def is_passthrough(value):
            current_system = self_ref()
            if current_system is None:
                return False
            return current_system._is_passthrough_predicate(value)

        self.is_immutable = is_immutable
        self.is_passthrough = is_passthrough

        def bind_and_mark(value):
            current_system = self_ref()
            if current_system is not None:
                current_system.is_bound.add(value)
            return binder.bind(value)

        unbind_instance = on_del or binder.unbind

        self.bind = bind_and_mark
        self.binder = binder
        self.original_to_retrace_type = {}
        self.retrace_to_original_type = {}
        self.extended_type_flags = {}
        
        self._refresh_type_predicates()
        
        self._current_phase = _space_dispatch(
            _phase_disabled,
            (
                (self.internal_space, _phase_internal),
                (self.external_space, _phase_external),
            ),
        )

        self.proxy_factory = ProxyFactory(
            binder=_MarkingBinder(binder, self.is_bound.add),
            gateway_pair=self.gateway_pair,
            on_del=unbind_instance,
            proxy_type_customizer=proxy_type_customizer,
        )

    def dispatch(self, *, internal, external, disabled):
        disp = _space_dispatch(disabled)
        disp[self.internal_space] = internal
        disp[self.external_space] = external
        return disp

    def _refresh_type_predicates(self):
        immutable_type_tuple = tuple(self.immutable_types)
        is_immutable_type = lambda cls: issubclass(cls, immutable_type_tuple)
        self._is_immutable_predicate = utils.FastTypePredicate(is_immutable_type).istypeof

        self._is_passthrough_predicate = utils.FastTypePredicate(
            lambda cls: is_immutable_type(cls) or issubclass(cls, ExtendedType)).istypeof

    @property
    def location(self):
        return self._current_phase()

    def enabled(self):
        return self.location is not None

    def _space_for(self, phase):
        if phase == "internal":
            return self.internal_space
        if phase == "external":
            return self.external_space
        if phase is None:
            return self.disabled_space
        raise ValueError(f"unknown Retrace phase: {phase!r}")

    def apply_with(self, phase, function):
        return functional.partial(_space_apply, self._space_for(phase), function)

    def record_replay_operation(self, recorder, replayer):
        return self._record_replay_operation(recorder, replayer)

    def run_internal(self, function, *args, **kwargs):
        return _space_apply(self.internal_space, function, *args, **kwargs)

    @contextmanager
    def enable(self):
        gc.collect()
        try:
            if callable(self.lifecycle_hooks.on_start):
                self.lifecycle_hooks.on_start()
            yield
        finally:
            if callable(self.lifecycle_hooks.on_end):
                self.lifecycle_hooks.on_end()

    def run(self, function, *args, **kwargs):
        previous_count = begin_suppress_monitoring()
        try:
            manager = self.enable()
            manager.__enter__()
        finally:
            end_suppress_monitoring(previous_count)

        try:
            result = self.run_internal(function, *args, **kwargs)
        except BaseException:
            exc_info = sys.exc_info()
            previous_count = begin_suppress_monitoring()
            try:
                suppress = manager.__exit__(*exc_info)
            finally:
                end_suppress_monitoring(previous_count)
            if not suppress:
                raise
        else:
            previous_count = begin_suppress_monitoring()
            try:
                manager.__exit__(None, None, None)
            finally:
                end_suppress_monitoring(previous_count)
            return result

    def disable_for(self, function, *, unwrap_args=True, retrace=True):
        disabled = fallback if unwrap_args else utils.try_unwrap_apply
        applied = self.apply_with(None, functional.partial(disabled, function))
        wrapped = disabled_callable(function, applied)
        if retrace:
            return globals()["retrace"].disable(wrapped)
        return wrapped

    def disabled_method_for(self, function, *, retrace=True):
        disabled_function = self.disable_for(
            function,
            unwrap_args=False,
            retrace=retrace,
        )

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return disabled_function(*args, **kwargs)

        return wrapper

    def install(self):
        return utils.noop

    def ext_proxy_result(self, fn):
        call_real = fn
        ext_proxy_result = functional.sequence(call_real, self.proxy_factory.proxy_external)
        wrapped = self.dispatch(
            disabled=call_real,
            external=ext_proxy_result,
            internal=ext_proxy_result,
        )
        standalone = wrapped_callable(wrapped)
        self.bind(standalone)
        return standalone

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler=handler, target=target)
        self.bind(wrapped)
        return wrapped

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

    def is_extended_type(self, cls):
        original = self.original_type_for(cls)
        if original is None:
            return False
        return self.extended_type_flags.get(original, {}).get("kind") == "extended"

    def is_extended_instance(self, obj):
        return self.is_extended_type(type(obj))

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

    def proxy_type(self, cls: type) -> type:
        try:
            return self.extend_type(cls, python_distribution=True)
        except TypeError:
            return self.wrap_type(cls)

    def wrap_type(self, cls: type) -> type:
        existing = self._existing_retrace_type(cls, kind="wrapped")
        if existing is not None:
            return existing

        retrace_type = self.proxy_factory.dynamic_external_type(cls)
        return self._register_retrace_type(cls, retrace_type, kind="wrapped")

    def patch_function(self, fn):
        if isinstance(fn, type) and self.retrace_type_for(fn) is None:
            self.wrap_type(fn)
        if not self.is_bound(fn):
            self.bind(fn)
        wrapped = self._wrapped_function(self.gateway_pair.external, fn)
        patched = utils.wrapped_callable(wrapped)
        self.bind(patched)
        return patched

    def add_immutable_type(self, cls):
        self.immutable_types.add(cls)
        self._refresh_type_predicates()

    def add_immutable_types(self, *classes):
        self.immutable_types.update(classes)
        self._refresh_type_predicates()

    def update_external(self, f):
        self.gateway_pair.external = f(self.gateway_pair.external)

    def set_on_call(self, on_call):
        external = self.gateway_pair.external
        observed = utils.observer(
            function=external,
            on_call=on_call,
        )
        self.gateway_pair.external = _space_dispatch(
            external,
            ((self.internal_space, observed),),
        )

    def _install_signal_capture(self, *, writer, encode_for_write):
        system_ref = weakref.ref(self)
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
        weakref.finalize(self, restore_signal)

    def _install_gc_capture(self, writer):
        system_ref = weakref.ref(self)
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
        weakref.finalize(self, remove_gc_callback)

    @staticmethod
    def record_system(
        *,
        writer: TraceWriter,
        debug : bool = False,
        async_capture: AsyncCapture = AsyncCapture(),
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> System:

        binder = stream.Binder(on_delete=writer.binding_delete)

        system = System(binder = binder, proxy_type_customizer = proxy_type_customizer)
        system.retrace_mode = "record"
        run = system.run

        def record_run(function, *args, **kwargs):
            writer.on_start()
            try:
                return run(function, *args, **kwargs)
            finally:
                writer.run_completed()

        system.run = record_run

        dynamic_ext_proxy_to_type = functional.when(
            system.proxy_factory.is_dynamic_external_proxy,
            functional.typeof,
        )

        extended_instance_to_type = functional.when(
            functional.and_predicate(
                functional.sequence(binder.lookup, operator.not_),
                functional.sequence(system.is_immutable, operator.not_),
                functional.sequence(
                    system.proxy_factory.is_dynamic_external_proxy,
                    operator.not_,
                ),
                functional.isinstanceof(ExtendedType),
            ),
            functional.typeof,
        )

        encode_for_write = functional.if_then_else(
            system.is_immutable,
            binder,
            functional.walker(functional.sequence(
                dynamic_ext_proxy_to_type,
                extended_instance_to_type,
                binder,
            )))

        on_result = functional.sequence(encode_for_write, writer.result)

        if async_capture.thread_switch:
            operation_result = system.internal_space.check_for_thread_switch(on_result)
            wrap_external_call = system.internal_space.check_for_thread_switch
        else:
            operation_result = on_result
            wrap_external_call = functional.identity

        operation_writer = _RecordReplayOperationWriter(
            result=operation_result,
        )

        def record_replay_operation(recorder, _replayer):
            return system.apply_with("external", recorder(operation_writer))

        system._record_replay_operation = record_replay_operation

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
            on_result = on_result,
            wrap_external_call = wrap_external_call,
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
            system._install_signal_capture(
                writer=writer,
                encode_for_write=encode_for_write)

        if async_capture.gc:
            system._install_gc_capture(writer)

        return system

    @staticmethod
    def replay_system(
        *,
        reader: TraceReader,
        debug : bool = False,
        proxy_type_customizer: ProxyTypeCustomizer = utils.noop,
    ) -> System:
        reader = _PeekableReader(reader)
        handoff = retrace.ThreadHandoff()
        bindings = {}
        cursors = _ThreadCursors()
        pending_coordinate_thread_id = [None]
        system = None
        materialize_replay_value = functional.identity
        unresolved_checkpoint_binding = object()

        resolve = functional.walker(functional.if_then_else(
            functional.isinstanceof(stream.Binding),
            functional.sequence(_binding_handle, bindings.__getitem__),
            functional.identity,
        ))

        def replay_value(value):
            return materialize_replay_value(resolve(value))

        def checkpoint_value(value):
            def resolve_checkpoint_binding(value):
                if not isinstance(value, stream.Binding):
                    return value
                return bindings.get(
                    _binding_handle(value),
                    unresolved_checkpoint_binding,
                )

            return materialize_replay_value(
                functional.walker(resolve_checkpoint_binding)(value)
            )

        def contains_unresolved_checkpoint_binding(value):
            if value is unresolved_checkpoint_binding:
                return True
            if isinstance(value, tuple | list):
                return any(contains_unresolved_checkpoint_binding(item) for item in value)
            if isinstance(value, dict):
                return any(
                    contains_unresolved_checkpoint_binding(item)
                    for pair in value.items()
                    for item in pair
                )
            return False
            
        def run_callback(message):
            try:
                return system.internal_space.apply(
                    functional.call,
                    replay_value(message.fn),
                    replay_value(message.args),
                    replay_value(message.kwargs),
                )
            except Exception:
                return None

        def consume_callback_completion(callback_result):
            try:
                message = reader.peek()
            except (LookupError, RuntimeError, StopIteration):
                return None
            if isinstance(message, CallbackResultMessage):
                message = reader()
                result = message.result
                if isinstance(result, stream.Binding):
                    bindings[_binding_handle(result)] = callback_result
                return message
            if isinstance(message, CallbackErrorMessage):
                return reader()
            return None

        def run_callback_envelope(message):
            callback_result = run_callback(message)
            consume_callback_completion(callback_result)

        def consume_armed_coordinate(expected_type=None):
            message = reader()
            if not isinstance(message, RunToCoordinateMessage):
                raise ReplayThreadScheduleError(
                    f"expected replay scheduler coordinate, got {message!r}"
                )
            pending_coordinate_thread_id[0] = None
            cursors.advance(_thread.get_ident(), message.cursor_delta)

            if expected_type is None:
                return message

            instruction = reader()
            if not isinstance(instruction, expected_type):
                raise ReplayThreadScheduleError(
                    f"expected replay scheduler instruction {expected_type!r}, "
                    f"got {instruction!r}"
                )
            return instruction

        def arm_next_coordinate():
            try:
                message = reader.peek()
            except (LookupError, RuntimeError, StopIteration):
                return False
            if not isinstance(message, RunToCoordinateMessage):
                return False

            thread_id = _thread.get_ident()
            cursor = cursors.after(thread_id, message.cursor_delta)
            pending_coordinate_thread_id[0] = thread_id
            instruction = reader.peek(1)

            if isinstance(instruction, SwitchThreadMessage):
                on_hit = functional.sequence(
                    functional.repeatedly(
                        consume_armed_coordinate,
                        SwitchThreadMessage,
                    ),
                    operator.attrgetter("thread_id"),
                    functional.partial(handoff.to),
                )
                error = ReplayThreadScheduleError(
                    f"overshot replay thread switch to {instruction.thread_id!r}"
                )
            elif isinstance(instruction, SignalMessage):
                on_hit = functional.sequence(
                    functional.repeatedly(consume_armed_coordinate, SignalMessage),
                    run_callback,
                )
                error = ReplayThreadScheduleError("overshot replay signal")
            elif isinstance(instruction, GCMessage):
                on_hit = functional.sequence(
                    functional.repeatedly(consume_armed_coordinate, GCMessage),
                    operator.attrgetter("generation"),
                    functional.partial(gc.collect),
                )
                error = ReplayThreadScheduleError("overshot replay gc")
            elif isinstance(instruction, CallbackMessage):
                on_hit = functional.sequence(
                    functional.repeatedly(consume_armed_coordinate, CallbackMessage),
                    run_callback_envelope,
                )
                error = ReplayThreadScheduleError("overshot replay callback")
            elif isinstance(instruction, RunCompletedMessage):
                on_hit = functional.repeatedly(
                    consume_armed_coordinate,
                    RunCompletedMessage,
                )
                error = ReplayThreadScheduleError("overshot replay completion")
            else:
                on_hit = functional.repeatedly(consume_armed_coordinate)
                error = ReplayThreadScheduleError(
                    f"overshot replay coordinate {cursor!r}"
                )

            if cursor is None:
                system.internal_space.call_at(cursor, on_hit)
            else:
                system.internal_space.call_at(
                    cursor,
                    on_hit,
                    functional.repeatedly(_raise, error),
                )
            return True

        def read_message():
            while True:
                try:
                    next_message = reader.peek()
                except (LookupError, RuntimeError, StopIteration):
                    raise
                if isinstance(next_message, RunToCoordinateMessage):
                    current_thread_id = _thread.get_ident()
                    scheduled_thread_id = pending_coordinate_thread_id[0]
                    if (
                        scheduled_thread_id is not None
                        and current_thread_id != scheduled_thread_id
                    ):
                        handoff.to(scheduled_thread_id)
                        continue
                    raise ReplayThreadScheduleError(
                        "replay reached a proxy boundary before the pending "
                        "scheduler coordinate fired"
                    )

                message = reader()
                if isinstance(message, BindCloseMessage):
                    bindings.pop(message.handle, None)
                    continue
                elif isinstance(message, OnStartMessage):
                    continue
                elif isinstance(message, SwitchThreadMessage):
                    raise RuntimeError(
                        f"switch thread without run-to-coordinate: {message!r}"
                    )
                elif isinstance(message, RunCompletedMessage):
                    continue
                elif isinstance(message, SignalMessage):
                    run_callback(message)
                    continue
                elif isinstance(message, GCMessage):
                    gc.collect(message.generation)
                    continue
                elif isinstance(message, CallbackMessage):
                    run_callback_envelope(message)
                    continue

                arm_next_coordinate()
                return message

        def consume_lifecycle_marker(message_type):
            while True:
                try:
                    message = reader.peek()
                except (LookupError, RuntimeError, StopIteration):
                    return None
                if isinstance(message, BindCloseMessage):
                    message = reader()
                    bindings.pop(message.handle, None)
                    continue
                if isinstance(message, message_type):
                    return reader()
                return None

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

            record_value = checkpoint_value(message.value)
            if contains_unresolved_checkpoint_binding(record_value):
                return None
            if record_value != value:
                from retracesoftware.install import ReplayDivergence
                raise ReplayDivergence(
                    f"checkpoint difference: {record_value!r} was expecting {value!r}"
                )

        def bind_unresolved_dynamic_external_result(value, args):
            if not isinstance(value, stream.Binding):
                return value
            handle = _binding_handle(value)
            if handle in bindings or not args:
                return value
            constructor_type = args[0]
            if (
                isinstance(constructor_type, type)
                and system.proxy_factory.is_dynamic_external_proxy_type(constructor_type)
            ):
                bindings[handle] = constructor_type
            return value

        @utils.striptraceback
        def next_result(*args, **_kwargs):
            message = read_message()
    
            if isinstance(message, ResultMessage):
                bind_unresolved_dynamic_external_result(message.result, args)
                return replay_value(message.result)
                
            if isinstance(message, ErrorMessage):
                raise replay_value(message.error)
                
            raise RuntimeError(f"expected replay result or error, got {message!r}")

        operation_reader = _RecordReplayOperationReader(result=next_result)

        def on_local_bind(value, binding):
            bindings[_binding_handle(binding)] = value

        def on_local_unbind(_value, binding):
            bindings.pop(_binding_handle(binding), None)

        replay_binder = _ReplayBinder(
            stream.Binder(
                on_delete=lambda handle: bindings.pop(_binding_handle(handle), None)
            ),
            on_bind=on_local_bind,
            on_unbind=on_local_unbind,
        )

        system = System(
            binder=replay_binder,
            proxy_type_customizer=proxy_type_customizer,
        )
        system.retrace_mode = "replay"
        def record_replay_operation(_recorder, replayer):
            return system.apply_with("external", replayer(operation_reader))

        system._record_replay_operation = record_replay_operation
        def replay_start():
            consume_lifecycle_marker(OnStartMessage)
            arm_next_coordinate()

        system.lifecycle_hooks = LifecycleHooks(
            on_start=replay_start,
            on_end=functional.partial(consume_lifecycle_marker, RunCompletedMessage),
        )
        system._arm_next_coordinate = arm_next_coordinate

        def materialize_external_type_token(value):
            if isinstance(value, type) and system.retrace_type_for(value) is not None:
                proxy_type = system.proxy_factory.typefactory.dynamic_external_type(value)
                return utils.create_wrapped(proxy_type, None)
            return system.proxy_factory.materialize_dynamic_external_proxy(value)

        materialize_replay_value = functional.walker(materialize_external_type_token)
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
