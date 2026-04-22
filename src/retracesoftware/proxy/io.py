"""IO bridge helpers for the gate-based ``System``."""
from contextlib import contextmanager
import retracesoftware.functional as functional
import retracesoftware.stream as stream
import retracesoftware.utils as utils
from retracesoftware.protocol.messages import (
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    ResultMessage,
    StacktraceMessage,
)
from retracesoftware.stream import (
    _BIND_OPEN_TAG,
    _is_bind_close,
    _is_bind_open,
)
from retracesoftware.stream.reader import ExpectedBindMarker, PeekableReader

from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System
import gc
import sys
import os


class _MarkerMessage:
    __slots__ = ("thread_id",)

    def __init__(self, thread_id=None):
        self.thread_id = thread_id

    def __repr__(self):
        return type(self).__name__


class OnStartMessage(_MarkerMessage):
    __slots__ = ()


class CallMarkerMessage(_MarkerMessage):
    __slots__ = ()


class CallbackResultMessage(ResultMessage):
    __slots__ = ()


class CallbackErrorMessage(ErrorMessage):
    __slots__ = ()


class CallbackMessage(CallMessage):
    __slots__ = ()


class _StampedProtocolSource:
    __slots__ = ("_close", "_read", "thread_id")

    def __init__(self, read, *, initial_thread_id, close=None):
        self._read = read
        self._close = close
        self.thread_id = initial_thread_id

    def __call__(self):
        return self.next()

    def next(self):
        while True:
            item = self._read()

            if item == "THREAD_SWITCH":
                next_thread_id = self._read()
                if next_thread_id is not None:
                    self.thread_id = next_thread_id
                continue

            return (self.thread_id, item)

    def close(self):
        if self._close is not None:
            return self._close()


class _RawTapeSource:
    __slots__ = ("_close", "_next_object", "_on_bind_close")

    def __init__(self, next_object, *, close=None):
        self._next_object = next_object
        self._close = close
        self._on_bind_close = None

    def read(self):
        while True:
            item = self._next_object()

            if _is_bind_close(item):
                if self._on_bind_close is not None:
                    self._on_bind_close(stream._bind_index(item))
                continue

            if _is_bind_open(item):
                return item

            if item == "NEW_BINDING":
                binding = self._next_object()
                while binding == "NEW_BINDING":
                    binding = self._next_object()
                handle = binding.handle if hasattr(binding, "handle") else binding
                return (_BIND_OPEN_TAG, handle)

            if item == "BINDING_DELETE":
                binding = self._next_object()
                while binding == "BINDING_DELETE":
                    binding = self._next_object()
                handle = binding.handle if hasattr(binding, "handle") else binding
                if self._on_bind_close is not None:
                    self._on_bind_close(handle)
                continue

            return item

    def close(self):
        if self._close is not None:
            return self._close()


class _BindOpenEvent:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index


class _BindCloseEvent:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index


class _ReplayBindingState:
    __slots__ = ("_bindings", "_close", "_peekable_reader")

    def __init__(self, source):
        read = source.read if hasattr(source, "read") else source
        self._peekable_reader = PeekableReader(read)
        self._bindings = {}
        self._close = getattr(source, "close", None)

    def resolve(self, obj, bindings=None):
        if bindings is None:
            bindings = self._bindings

        def transform(value):
            if isinstance(value, stream.Binding):
                return bindings[value.handle]
            return value

        return functional.walker(transform)(obj)

    def _normalize(self, value, bindings=None, *, resolve=True):
        if _is_bind_open(value):
            return _BindOpenEvent(stream._bind_index(value))
        if _is_bind_close(value):
            return _BindCloseEvent(stream._bind_index(value))
        if resolve:
            return self.resolve(value, bindings)
        return value

    def _peek_item(self, bindings=None, *, resolve=True):
        if bindings is None:
            bindings = self._bindings

        peekable = self._peekable_reader
        if not peekable._buffer:
            peekable._buffer.append(peekable.source())

        return self._normalize(peekable._buffer[0], bindings, resolve=resolve)

    def _discard_peeked(self):
        peekable = self._peekable_reader
        if peekable._buffer:
            peekable._buffer.popleft()
        else:
            peekable.source()

    def consume_pending_closes(self, *, ignore_end_of_stream=False):
        while True:
            try:
                value = self._peek_item(resolve=False)
            except StopIteration:
                return
            except RuntimeError:
                if ignore_end_of_stream:
                    return
                raise

            if not isinstance(value, _BindCloseEvent):
                return

            self._bindings.pop(value.index, None)
            self._discard_peeked()

    def read(self):
        self.consume_pending_closes()
        value = self._peek_item()

        if isinstance(value, _BindOpenEvent):
            raise RuntimeError("bind marker returned when bind was expected")

        self._discard_peeked()
        return value

    def read_raw(self):
        self.consume_pending_closes()
        value = self._peek_item(resolve=False)

        if isinstance(value, _BindOpenEvent):
            raise RuntimeError("bind marker returned when bind was expected")

        self._discard_peeked()
        return value

    def bind(self, obj):
        self.consume_pending_closes()
        value = self._peek_item(resolve=False)

        if not isinstance(value, _BindOpenEvent):
            raise ExpectedBindMarker(value)

        self._discard_peeked()
        self._bindings[value.index] = obj

    def bind_handle(self, handle, obj):
        self._bindings[handle] = obj

    def delete_handle(self, handle):
        self._bindings.pop(handle, None)

    def close(self):
        if self._close is not None:
            return self._close()


def _read_thread_id(thread_id):
    if callable(thread_id):
        return thread_id()
    return thread_id


class _ThreadDemuxSource:
    __slots__ = ("_close", "_dispatcher", "_peekable_reader", "_source", "_thread_id")

    def __init__(self, next_object, *, thread_id, initial_thread_id, close=None):
        self._source = _StampedProtocolSource(
            next_object,
            initial_thread_id=initial_thread_id,
            close=close,
        )
        self._peekable_reader = PeekableReader(self._source)
        self._dispatcher = utils.Dispatcher(self._peekable_reader)
        self._thread_id = thread_id
        self._close = close

    def _next_item(self):
        _, item = self._dispatcher.next(
            lambda entry: entry[0] == self._thread_id()
        )
        return item

    def read(self):
        return self._next_item()

    def close(self):
        if self._close is not None:
            return self._close()


class _IoMessageSource:
    __slots__ = ("_close", "_read", "_thread_id")

    def __init__(self, read, *, thread_id, close=None):
        self._read = read
        self._thread_id = thread_id
        self._close = close

    def read(self):
        return _message_from_tag(self._read(), self._read, self._thread_id)

    def close(self):
        if self._close is not None:
            return self._close()


def _message_from_tag(tag, reader, thread_id=None):
    current_thread_id = _read_thread_id(thread_id)

    if tag == "ON_START":
        return OnStartMessage(current_thread_id)
    if tag == "CALL":
        return CallMarkerMessage(current_thread_id)
    if tag == "CALLBACK":
        return CallbackMessage(
            reader(),
            reader(),
            reader(),
            thread_id=current_thread_id,
        )
    if tag == "RESULT":
        return ResultMessage(reader(), thread_id=current_thread_id)
    if tag == "ERROR":
        return ErrorMessage(reader(), thread_id=current_thread_id)
    if tag == "CALLBACK_RESULT":
        return CallbackResultMessage(reader(), thread_id=current_thread_id)
    if tag == "CALLBACK_ERROR":
        return CallbackErrorMessage(reader(), thread_id=current_thread_id)
    if tag == "CHECKPOINT":
        return CheckpointMessage(reader(), thread_id=current_thread_id)
    if tag == "STACKTRACE":
        return StacktraceMessage(reader(), thread_id=current_thread_id)

    return tag

def equal(a, b):
    if a is b:
        return True

    a_cls = type(a)
    b_cls = type(b)

    if issubclass(a_cls, utils.ExternalWrapped) and issubclass(b_cls, utils.ExternalWrapped):
        return True

    if a_cls is not b_cls:
        return False

    cls = a_cls

    if issubclass(cls, utils.ExternalWrapped):
        return True

    if cls is tuple or cls is list:
        if len(a) != len(b):
            return False            
        return all(equal(a[i], b[i]) for i in range(len(a)))

    if cls is dict:
        if len(a) != len(b):
            return False
        if a.keys() != b.keys():
            return False
        return all(equal(a[k], b[k]) for k in a.keys())

    return a == b


def _callable_debug_identity(value):
    try:
        unwrapped = utils.try_unwrap(value)
    except Exception:
        unwrapped = value

    def describe(obj):
        module = getattr(obj, "__module__", None)
        qualname = getattr(obj, "__qualname__", None)
        name = getattr(obj, "__name__", None)
        typename = type(obj).__name__
        identity = qualname or name or object.__repr__(obj)
        if module:
            identity = f"{module}.{identity}"
        return f"{typename}:{identity}"

    if unwrapped is value:
        return describe(value)
    return f"{describe(value)} -> {describe(unwrapped)}"


def _safe_debug_value(value, depth=0):
    if depth > 2:
        return f"<{type(value).__name__}>"

    if isinstance(value, (str, int, float, bool, type(None), bytes)):
        return repr(value)

    if callable(value):
        return _callable_debug_identity(value)

    if isinstance(value, tuple):
        inner = ", ".join(_safe_debug_value(v, depth + 1) for v in value[:4])
        if len(value) > 4:
            inner += ", ..."
        return f"({inner})"

    if isinstance(value, list):
        inner = ", ".join(_safe_debug_value(v, depth + 1) for v in value[:4])
        if len(value) > 4:
            inner += ", ..."
        return f"[{inner}]"

    if isinstance(value, dict):
        items = list(value.items())[:4]
        inner = ", ".join(
            f"{_safe_debug_value(k, depth + 1)}: {_safe_debug_value(v, depth + 1)}"
            for k, v in items
        )
        if len(value) > 4:
            inner += ", ..."
        return "{" + inner + "}"

    try:
        return object.__repr__(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _on_call(fn, *args, **kwargs):
    return {
        "function": fn,
        "args": args,
        "kwargs": kwargs,
    }

def _on_result(result): return {"result": result}

def _on_error(error): return {"error": error}


def _binding_handle(binding):
    return binding.handle if hasattr(binding, "handle") else binding

def normalize_stack_delta(delta):
    to_drop, frames = delta
    return (
        to_drop,
        tuple(
            (frame.filename, frame.lineno)
            if isinstance(frame, utils.Stack)
            else tuple(frame)
            for frame in frames
        ),
    )

def recorder(*, 
    writer,
    # tape_writer: TapeWriter, 
    debug: bool = False,
    stacktraces: bool = False,
    gc_collect_multiplier: int = None) -> System:

    def tagged(tag):
        return functional.partial(writer, tag)
    
    on_thread_switch = functional.sequence(
        functional.repeatedly(lambda: system.thread_id()),
        tagged("THREAD_SWITCH"))

    def threaded(writer): 
        return utils.thread_switch(writer, on_thread_switch=on_thread_switch
    )

    binder = stream.Binder(
        on_delete=functional.sequence(_binding_handle, tagged("BINDING_DELETE"))
    )

    system = System(
        functional.sequence(binder.bind, _binding_handle, threaded(tagged("NEW_BINDING")))
    )

    add_bindings = functional.walker(binder)

    def binding_writer(writer):
        return functional.sequence(add_bindings, writer)

    def write_callback(fn, args, kwargs):
        writer(
            "CALLBACK",
            add_bindings(fn),
            add_bindings(args),
            add_bindings(kwargs),
        )

    # write = functional.mapargs(
    #     function=threaded_write,
    #     transform=functional.walker(binder),
    # )

    in_sandbox = system._in_sandbox

    def on_start(*args, **kwargs):
        writer("ON_START")

    checkpoint = binding_writer(tagged("CHECKPOINT"))

    call = functional.sequence(_on_call, checkpoint) \
        if debug else functional.repeatedly(writer, "CALL")
    # on_start = check_thread_switch(functional.repeatedly(write, "ON_START"))

    if stacktraces:
        stack = utils.StackFactory()
        stacktrace = tagged("STACKTRACE")

        write_stacktrace = functional.repeatedly(functional.if_then_else(
            lambda: in_sandbox(),
            functional.sequence(stack.delta, normalize_stack_delta, stacktrace),
            utils.noop))

        call = utils.observer(on_call=system.disable_for(write_stacktrace), function=call)
        on_start = functional.sequence(functional.repeatedly(stack.delta), on_start)

    def get_error(*funcs):
        return functional.sequence(functional.positional_param(1), *funcs)

    on_callback_result = functional.sequence(_on_result, checkpoint) \
        if debug else binding_writer(tagged("CALLBACK_RESULT"))

    on_callback_error = get_error(_on_error, checkpoint) \
        if debug else tagged("CALLBACK_ERROR")

    create_stub_object = system.wrap_async(utils.create_stub_object)
    collect = system.wrap_async(gc.collect)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system._in_sandbox),
        checkpoint, utils.noop)

    system.lifecycle_hooks = LifecycleHooks(
            on_start = on_start,
            on_end = None,
            #functional.repeatedly(write, "ON_END")
            )
            
    on_call = threaded(functional.pack_call(1, write_callback))

    on_result = threaded(binding_writer(tagged("RESULT")))

    on_error = threaded(functional.sequence(
        functional.positional_param(1), 
        functional.if_then_else(
            functional.isinstanceof(Exception),
            tagged("ERROR"),
            utils.noop)))

    if gc_collect_multiplier:
        gc_collector = utils.Collector(multiplier = gc_collect_multiplier, collect = collect)
        on_result = utils.observer(on_call = gc_collector, function = on_result)
        
    system.primary_hooks = CallHooks(
            on_call = on_call,
            on_result = on_result,
            on_error = on_error)

    system.secondary_hooks = CallHooks(
        on_call=call, 
        on_result=on_callback_result,
        on_error=on_callback_error)

    @utils.exclude_from_stacktrace
    def async_new_patched(obj):
        cls = type(obj)
        assert cls in system.patched_types, (
            "async_new_patched expected a patched type, got "
            f"{cls.__module__}.{cls.__qualname__}"
        )
        assert binder.lookup(cls) is not None, (
            "async_new_patched expected the patched type to be bound, got "
            f"{cls.__module__}.{cls.__qualname__}"
        )
        system.primary_hooks.on_call(create_stub_object, cls)
        system.bind(obj)
        system.secondary_hooks.on_result(obj)

    system.async_new_patched = async_new_patched

    system.passthrough_proxyref = True

    return system

@contextmanager
def recorder_context(**kwargs):
    system = recorder(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()

def default_unexpected_handler(key):
    print(f"Unexpected message: {key}, was expecting a result, error, or call", file=sys.stderr)
    os._exit(1)

def default_desync_handler(record, replay):
    print(f"Checkpoint difference: {_safe_debug_value(record)} was expecting {_safe_debug_value(replay)}", file=sys.stderr)
    os._exit(1)

def replayer(*, next_object,
             close=None,
             on_unexpected = default_unexpected_handler,
             on_desync = default_desync_handler,
             debug: bool = False,
             stacktraces: bool = False) -> System:
    system = None

    def current_thread_id():
        if system is None:
            return 0
        return system.thread_id()

    raw_source = _RawTapeSource(next_object, close=close)
    thread_source = _ThreadDemuxSource(
        raw_source.read,
        thread_id=current_thread_id,
        initial_thread_id=current_thread_id(),
        close=raw_source.close,
    )
    tape_reader = _ReplayBindingState(thread_source)
    raw_source._on_bind_close = tape_reader.delete_handle
    message_source = _IoMessageSource(
        tape_reader.read,
        thread_id=current_thread_id,
        close=tape_reader.close,
    )
    raw_message_source = _IoMessageSource(
        tape_reader.read_raw,
        thread_id=current_thread_id,
        close=tape_reader.close,
    )

    read_message = message_source.read
    read_raw_message = raw_message_source.read
    system = System(tape_reader.bind)
    system.replay_materialize = set()
    materializing = utils.ThreadLocal(False)

    stack = utils.StackFactory()
    current_stack = utils.ThreadLocal([])

    def trim_replay_stack(replay, recorded):
        if len(replay) >= len(recorded):
            for start in range(len(replay) - len(recorded) + 1):
                candidate = replay[start:start + len(recorded)]
                if candidate == recorded:
                    return candidate
        return replay

    def on_stacktrace():
        message = expect_message(StacktraceMessage)
        to_drop, new_frames = message.stacktrace
        this_stack = current_stack.get()
        del this_stack[:to_drop]
        this_stack[:0] = [
            (frame.filename, frame.lineno)
            if isinstance(frame, utils.Stack)
            else tuple(frame)
            for frame in new_frames
        ]

        replay = trim_replay_stack(list(stack())[2:], this_stack)
        if replay[1:] != this_stack[1:]:
            on_desync(replay, this_stack)
        
    @utils.exclude_from_stacktrace
    def expect_message(expected_type):
        message = read_message()

        if isinstance(message, expected_type):
            return message

        if isinstance(message, CallbackMessage):
            run_callback(message)
            return expect_message(expected_type)

        on_desync(message, expected_type)

    def run_callback(message):
        try:
            return functional.call(message.fn, message.args, message.kwargs)
        except Exception:
            return None

    def run_raw_callback(message):
        try:
            return functional.call(
                tape_reader.resolve(message.fn),
                tape_reader.resolve(message.args),
                tape_reader.resolve(message.kwargs),
            )
        except Exception:
            return None
            
    # safeequal = system.disable_for(equal)
    def diff(record, replay):
        if not equal(record, replay):
            on_desync(record, replay)

    def checkpoint(replay):
        message = expect_message(CheckpointMessage)
        diff(record = message.value, replay = replay)

    in_sandbox = system._in_sandbox

    call = functional.sequence(
        _on_call, 
        checkpoint) if debug else functional.repeatedly(expect_message, CallMarkerMessage)

    if stacktraces:
        def next_stacktrace(*args, **kwargs):
            if in_sandbox():
                on_stacktrace()
                
        call = functional.sequence(functional.side_effect(system.disable_for(next_stacktrace)), call)

    system.wrap_async(utils.create_stub_object)
    system.wrap_async(gc.collect)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system._in_sandbox),
        checkpoint, utils.noop)

    def next_materialized_result(replay):
        while True:
            message = read_raw_message()

            if isinstance(message, CallbackMessage):
                run_raw_callback(message)
                continue

            if isinstance(message, (CallbackResultMessage, CallbackErrorMessage, CallMarkerMessage)):
                continue

            if isinstance(message, ResultMessage):
                record = message.result
                if isinstance(record, stream.Binding):
                    tape_reader.bind_handle(record.handle, replay)
                    return replay
                return tape_reader.resolve(record)

            if isinstance(message, ErrorMessage):
                raise message.error

            return system.disable_for(on_unexpected)(message)

    def on_materialized_result(replay):
        if not materializing.get():
            return None

        materializing.set(False)
        record = next_materialized_result(replay)
        diff(record=record, replay=replay)

    def on_materialized_error(exc_type, exc_value, exc_tb):
        if not materializing.get():
            return None

        materializing.set(False)

        while True:
            message = read_raw_message()

            if isinstance(message, CallbackMessage):
                run_raw_callback(message)
                continue

            if isinstance(message, (CallbackResultMessage, CallbackErrorMessage, CallMarkerMessage)):
                continue

            if isinstance(message, ErrorMessage):
                if type(message.error) is not exc_type or str(message.error) != str(exc_value):
                    on_desync(message.error, exc_value)
                return None

            return system.disable_for(on_unexpected)(message)

    system.primary_hooks = CallHooks(
        on_call=None,
        on_result=on_materialized_result,
        on_error=on_materialized_error,
    )
    system.async_new_patched = system.bind

    on_callback_result = functional.sequence(
        _on_result, checkpoint) if debug else functional.repeatedly(expect_message, CallbackResultMessage)
    on_callback_error = functional.sequence(
        functional.positional_param(1), 
        _on_error, 
        checkpoint) if debug else functional.repeatedly(expect_message, CallbackErrorMessage)

    system.secondary_hooks = CallHooks(
        on_call=call, 
        on_result=on_callback_result,
        on_error=on_callback_error)

    def on_start():
        stack.delta() # reset the stack position for delta
        expect_message(OnStartMessage)

    def on_end():
        tape_reader.consume_pending_closes(ignore_end_of_stream=True)

    system.lifecycle_hooks=LifecycleHooks(
        on_start=on_start,
        on_end=on_end
        # on_end=functional.repeatedly(expect, "ON_END"),
        )

    def next_result_message():
        while True:
            message = read_message()

            if isinstance(message, CallbackMessage):
                run_callback(message)
                continue

            if isinstance(message, (CallbackResultMessage, CallbackErrorMessage)):
                continue

            if isinstance(message, ResultMessage):
                return message.result

            if isinstance(message, ErrorMessage):
                raise message.error

            return system.disable_for(on_unexpected)(message)

    @utils.exclude_from_stacktrace
    def ext_execute(fn, *args, **kwargs):
        return next_result_message()

    system.ext_execute = ext_execute

    return system
