"""IO bridge helpers for the gate-based ``System``."""
from collections import deque
from contextlib import contextmanager
import enum
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

from retracesoftware.proxy.system import (
    CallHooks,
    LifecycleHooks,
    ProxyRef,
    System,
)
import gc
import sys
import os
import types
from retracesoftware.proxy.gateway import ext_replay_gateway, int_replay_gateway

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
    __slots__ = ("_bindings", "_buffers", "_close", "_read", "_source", "_thread_id")

    def __init__(self, source):
        read = source.read if hasattr(source, "read") else source
        self._read = read
        self._source = source
        self._thread_id = getattr(source, "_thread_id", lambda: None)
        self._buffers = {}
        self._bindings = {}
        self._close = getattr(source, "close", None)

    def resolve(self, obj, bindings=None):
        if bindings is None:
            bindings = self._bindings

        def transform(value):
            if isinstance(value, stream.Binding):
                value = bindings[value.handle]
            if isinstance(value, ProxyRef):
                return value()
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

        buffer = self._buffers.setdefault(self._thread_id(), deque())
        if not buffer:
            buffer.append(self._read())

        return self._normalize(buffer[0], bindings, resolve=resolve)

    def _peek_buffered_item(self, bindings=None, *, resolve=True):
        if bindings is None:
            bindings = self._bindings

        thread_id = self._thread_id()
        buffer = self._buffers.setdefault(thread_id, deque())
        if not buffer:
            peek_buffered = getattr(self._source, "peek_buffered", None)
            if peek_buffered is None:
                raise LookupError(thread_id)
            buffer.append(peek_buffered())

        return self._normalize(buffer[0], bindings, resolve=resolve)

    def _discard_peeked(self):
        buffer = self._buffers.setdefault(self._thread_id(), deque())
        if buffer:
            buffer.popleft()
        else:
            self._read()

    def consume_pending_closes(self, *, ignore_end_of_stream=False, buffered_only=False):
        while True:
            try:
                peek = self._peek_buffered_item if buffered_only else self._peek_item
                value = peek(resolve=False)
            except LookupError:
                return
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

    def lookup_handle(self, handle):
        return self._bindings[handle]

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

    def peek_buffered(self):
        current_thread_id = self._thread_id()

        try:
            buffered = self._dispatcher.buffered
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            buffered = None
        else:
            if buffered[0] == current_thread_id:
                return buffered[1]

        for entry in self._peekable_reader._buffer:
            if entry[0] == current_thread_id:
                return entry[1]

        raise LookupError(current_thread_id)

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


def _normalized_call_args(fn, args):
    target = _unwrap_callable(fn)

    objclass = getattr(target, "__objclass__", None)
    if objclass is not None and args and isinstance(args[0], objclass):
        return args[1:]
    return args


def _is_socketpair_function(fn):
    return (
        getattr(fn, "__module__", "") == "_socket"
        and getattr(fn, "__name__", "") == "socketpair"
    )


def _is_descriptor_get_function(fn):
    objclass = getattr(fn, "__objclass__", None)
    return (
        getattr(fn, "__name__", "") == "__get__"
        and getattr(objclass, "__name__", "") in {"getset_descriptor", "member_descriptor"}
    )


def _is_dynamic_proxy_shim(fn):
    return (
        getattr(fn, "__qualname__", "")
        == "_ext_proxytype_from_spec.<locals>.unbound_function.<locals>.<lambda>"
    )


def _socketpair_args_with_defaults(args):
    if len(args) > 3:
        return args

    socket_module = sys.modules.get("_socket")
    if socket_module is None:
        return args

    family = getattr(
        socket_module,
        "AF_UNIX",
        getattr(socket_module, "AF_INET", None),
    )
    kind = getattr(socket_module, "SOCK_STREAM", None)
    if family is None or kind is None:
        return args

    defaults = (family, kind, 0)
    return tuple(args) + defaults[len(args):]


def _descriptor_get_args_without_owner(args):
    if len(args) == 3 and args[1] is not None:
        return args[:2]
    return args


def _equal_call_payload(a, b):
    fn_a = a.get("function")
    fn_b = b.get("function")
    target_a = _unwrap_callable(fn_a)
    target_b = _unwrap_callable(fn_b)
    same_dynamic_proxy_shim = (
        _is_dynamic_proxy_shim(target_a)
        and _is_dynamic_proxy_shim(target_b)
    )

    if not equal(fn_a, fn_b) and not same_dynamic_proxy_shim:
        return False

    args_a = a.get("args", ())
    args_b = b.get("args", ())
    kwargs_a = a.get("kwargs", {})
    kwargs_b = b.get("kwargs", {})
    normalized_a = _normalized_call_args(fn_a, args_a)
    normalized_b = _normalized_call_args(fn_b, args_b)
    args_equal = equal(normalized_a, normalized_b)
    if kwargs_a == kwargs_b and args_equal:
        return True

    if kwargs_a == kwargs_b and _is_socketpair_function(target_a):
        return (
            _socketpair_args_with_defaults(normalized_a)
            == _socketpair_args_with_defaults(normalized_b)
        )

    if kwargs_a == kwargs_b and _is_descriptor_get_function(target_a):
        return equal(
            _descriptor_get_args_without_owner(normalized_a),
            _descriptor_get_args_without_owner(normalized_b),
        )

    if (
        not args_a
        or not args_b
    ) and (
        getattr(target_a, "__objclass__", None) is not None
        or same_dynamic_proxy_shim
    ):
        return True

    return False

def equal(a, b):
    if a is b:
        return True

    if isinstance(a, memoryview) and isinstance(b, (bytes, bytearray)):
        return a.tobytes() == bytes(b)
    if isinstance(b, memoryview) and isinstance(a, (bytes, bytearray)):
        return b.tobytes() == bytes(a)

    if _is_checkpoint_external_marker(a):
        return _checkpoint_marker_matches_value(a, b)
    if _is_checkpoint_external_marker(b):
        return _checkpoint_marker_matches_value(b, a)
    if _is_checkpoint_descriptor_marker(a):
        return _checkpoint_descriptor_marker_matches_value(a, b)
    if _is_checkpoint_descriptor_marker(b):
        return _checkpoint_descriptor_marker_matches_value(b, a)
    if _is_checkpoint_enum_marker(a):
        return _checkpoint_enum_marker_matches_value(a, b)
    if _is_checkpoint_enum_marker(b):
        return _checkpoint_enum_marker_matches_value(b, a)
    if _is_checkpoint_exception_marker(a):
        return _checkpoint_exception_marker_matches_value(a, b)
    if _is_checkpoint_exception_marker(b):
        return _checkpoint_exception_marker_matches_value(b, a)
    if _is_checkpoint_traceback_marker(a):
        return _checkpoint_traceback_marker_matches_value(a, b)
    if _is_checkpoint_traceback_marker(b):
        return _checkpoint_traceback_marker_matches_value(b, a)

    unwrapped_a = _unwrap_callable(a)
    unwrapped_b = _unwrap_callable(b)
    if unwrapped_a is not a or unwrapped_b is not b:
        return equal(unwrapped_a, unwrapped_b)

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
        if (
            a.keys() == b.keys() == {"function", "args", "kwargs"}
            and "function" in a
            and "args" in a
            and "kwargs" in a
        ):
            return _equal_call_payload(a, b)
        if len(a) != len(b):
            return False
        if a.keys() != b.keys():
            return False
        return all(equal(a[k], b[k]) for k in a.keys())

    return a == b


def _callable_debug_identity(value):
    unwrapped = _unwrap_callable(value)

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

_CHECKPOINT_EXTERNAL_MARKER = "__retrace_checkpoint_external__"
_CHECKPOINT_DESCRIPTOR_MARKER = "__retrace_checkpoint_descriptor__"
_CHECKPOINT_ENUM_MARKER = "__retrace_checkpoint_enum__"
_CHECKPOINT_EXCEPTION_MARKER = "__retrace_checkpoint_exception__"
_CHECKPOINT_TRACEBACK_MARKER = "__retrace_checkpoint_traceback__"


def _external_type_key(value):
    cls = type(value)
    return (
        getattr(cls, "__module__", ""),
        getattr(cls, "__qualname__", getattr(cls, "__name__", "")),
    )


def _unwrap_callable(value):
    target = getattr(value, "_retrace_wrapped", value)
    try:
        return utils.try_unwrap(target)
    except Exception:
        return target


def _checkpoint_external_type_key(value):
    if isinstance(value, ProxyRef):
        cls = value.cls
    elif isinstance(value, type) and issubclass(value, utils.ExternalWrapped):
        cls = value
    elif isinstance(value, utils.ExternalWrapped):
        cls = type(value)
    else:
        return None

    return (
        getattr(cls, "__module__", ""),
        getattr(cls, "__qualname__", getattr(cls, "__name__", "")),
    )


def _checkpoint_external_marker(value):
    type_key = _checkpoint_external_type_key(value)
    if type_key is None:
        return value
    return (_CHECKPOINT_EXTERNAL_MARKER, *type_key)


def _raw_checkpoint_descriptor_key(value):
    if isinstance(value, utils.ExternalWrapped):
        return None

    descriptor_type_name = type(value).__name__
    if descriptor_type_name not in ("getset_descriptor", "member_descriptor"):
        return None

    owner = getattr(value, "__objclass__", None)
    if owner is None:
        return None

    return (
        descriptor_type_name,
        getattr(owner, "__module__", ""),
        getattr(owner, "__qualname__", getattr(owner, "__name__", "")),
        getattr(value, "__name__", ""),
    )


def _checkpoint_descriptor_key(value):
    descriptor_key = _raw_checkpoint_descriptor_key(value)
    if descriptor_key is not None:
        return descriptor_key

    try:
        if isinstance(value, utils._WrappedBase):
            unwrapped = utils.unwrap(value)
        else:
            unwrapped = utils.try_unwrap(value)
    except Exception:
        return None

    if unwrapped is value:
        return None
    return _raw_checkpoint_descriptor_key(unwrapped)


def _checkpoint_descriptor_marker(value):
    descriptor_key = _checkpoint_descriptor_key(value)
    if descriptor_key is None:
        return value
    return (_CHECKPOINT_DESCRIPTOR_MARKER, *descriptor_key)


def _checkpoint_descriptor_proxy_type_name(value):
    if not isinstance(value, utils.ExternalWrapped):
        return None

    cls = type(value)
    type_name = getattr(cls, "__qualname__", getattr(cls, "__name__", ""))
    if type_name in ("getset_descriptor", "member_descriptor"):
        return type_name
    return None


def _checkpoint_enum_key(value):
    if not isinstance(value, enum.Enum):
        return None

    cls = type(value)
    return (
        getattr(cls, "__module__", ""),
        getattr(cls, "__qualname__", getattr(cls, "__name__", "")),
        value.value,
    )


def _checkpoint_enum_marker(value):
    enum_key = _checkpoint_enum_key(value)
    if enum_key is None:
        return value
    return (_CHECKPOINT_ENUM_MARKER, *enum_key)


def _checkpoint_exception_type_key(value):
    if not isinstance(value, BaseException):
        return None

    cls = type(value)
    return (
        getattr(cls, "__module__", ""),
        getattr(cls, "__qualname__", getattr(cls, "__name__", "")),
    )


def _checkpoint_exception_marker(value):
    type_key = _checkpoint_exception_type_key(value)
    if type_key is None:
        return value
    return (_CHECKPOINT_EXCEPTION_MARKER, *type_key)


def _checkpoint_traceback_marker(value):
    if not isinstance(value, types.TracebackType):
        return value
    return (_CHECKPOINT_TRACEBACK_MARKER,)


def _checkpoint_stable_marker(value):
    for marker in (
        _checkpoint_descriptor_marker,
        _checkpoint_enum_marker,
        _checkpoint_external_marker,
        _checkpoint_exception_marker,
        _checkpoint_traceback_marker,
    ):
        marked = marker(value)
        if marked is not value:
            return marked
    return value


def _is_checkpoint_external_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and value[0] == _CHECKPOINT_EXTERNAL_MARKER
    )


def _is_checkpoint_descriptor_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 5
        and value[0] == _CHECKPOINT_DESCRIPTOR_MARKER
    )


def _is_checkpoint_enum_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 4
        and value[0] == _CHECKPOINT_ENUM_MARKER
    )


def _is_checkpoint_exception_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and value[0] == _CHECKPOINT_EXCEPTION_MARKER
    )


def _is_checkpoint_traceback_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 1
        and value[0] == _CHECKPOINT_TRACEBACK_MARKER
    )


def _checkpoint_marker_matches_value(marker, value):
    if _is_checkpoint_external_marker(value):
        return marker == value
    return marker[1:] == _checkpoint_external_type_key(value)


def _checkpoint_descriptor_marker_matches_value(marker, value):
    if _is_checkpoint_descriptor_marker(value):
        return marker == value
    key = _checkpoint_descriptor_key(value)
    if marker[1:] == key:
        return True

    return marker[1] == _checkpoint_descriptor_proxy_type_name(value)


def _checkpoint_enum_marker_matches_value(marker, value):
    if _is_checkpoint_enum_marker(value):
        return marker == value
    return marker[1:] == _checkpoint_enum_key(value)


def _checkpoint_exception_marker_matches_value(marker, value):
    if _is_checkpoint_exception_marker(value):
        return marker == value
    return marker[1:] == _checkpoint_exception_type_key(value)


def _checkpoint_traceback_marker_matches_value(marker, value):
    if _is_checkpoint_traceback_marker(value):
        return True
    return isinstance(value, types.TracebackType)


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

    system = System()
    system.retrace_mode = "record"

    write_thread_switch = functional.sequence(
        functional.repeatedly(system.thread_id),
        functional.partial(writer, "THREAD_SWITCH"),
    )
    write = utils.thread_switch(writer, on_thread_switch=write_thread_switch)

    def tagged(tag):
        return functional.partial(write, tag)

    binder = stream.Binder(
        on_delete=functional.sequence(_binding_handle, tagged("BINDING_DELETE"))
    )
    system.bind = utils.runall(
        system.is_bound.add,
        functional.sequence(binder.bind, _binding_handle, tagged("NEW_BINDING")),
    )

    for runtime_obj in (
        writer,
        getattr(writer, "__self__", None),
        getattr(writer, "__func__", None),
        getattr(getattr(writer, "__self__", None), "_write_lock", None),
        getattr(getattr(writer, "__self__", None), "_write_object", None),
        write,
        write_thread_switch,
        tagged,
    ):
        if runtime_obj is not None:
            system.is_bound.add(runtime_obj)
    system.bind(system.ext_proxytype_from_spec)
    system.bind(system)

    add_bindings = functional.walker(binder)

    def binding_writer(writer):
        return functional.sequence(add_bindings, writer)

    def write_callback(fn, args, kwargs):
        write(
            "CALLBACK",
            add_bindings(fn),
            add_bindings(args),
            add_bindings(kwargs),
        )

    # write = functional.mapargs(
    #     function=threaded_write,
    #     transform=functional.walker(binder),
    # )

    in_sandbox = system.enabled

    on_start = functional.repeatedly(write, "ON_START")

    checkpoint = functional.sequence(
        functional.walker(_checkpoint_stable_marker),
        binding_writer(tagged("CHECKPOINT")),
    )

    call = functional.sequence(_on_call, checkpoint) \
        if debug else functional.repeatedly(write, "CALL")
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
        functional.repeatedly(system.enabled),
        checkpoint, utils.noop)
    system.sync = system.disable_for(
        functional.repeatedly(write, "SYNC"),
        unwrap_args=False,
    )

    system.lifecycle_hooks = LifecycleHooks(
            on_start = on_start,
            on_end = None,
            #functional.repeatedly(write, "ON_END")
            )
            
    on_call = functional.pack_call(1, write_callback)

    on_result = functional.sequence(
        system.serialize_ext_wrapped,
        binding_writer(tagged("RESULT")),
    )

    on_error = functional.sequence(
        functional.positional_param(1), 
        functional.if_then_else(
            functional.isinstanceof(Exception),
            tagged("ERROR"),
            utils.noop))

    if gc_collect_multiplier:
        gc_collector = utils.Collector(multiplier = gc_collect_multiplier, collect = collect)
        on_result = utils.observer(on_call = gc_collector, function = on_result)
    
    system.primary_hooks = CallHooks(
        on_call = on_call,
        on_result = on_result,
        on_error = on_error,
    )
    system.secondary_hooks = CallHooks(
        on_call = call,
        on_result = on_callback_result,
        on_error = on_callback_error,    
    )

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
             close = None,
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
    on_unexpected = system.disable_for(on_unexpected, unwrap_args=False)
    on_desync = system.disable_for(on_desync, unwrap_args=False)
    system.retrace_mode = "replay"
    system.replay_materialize = set()

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
        expected_is_type = (
            isinstance(expected_type, type)
            or (
                isinstance(expected_type, tuple)
                and all(isinstance(item, type) for item in expected_type)
            )
        )

        if expected_is_type:
            if isinstance(message, expected_type):
                return message
        elif message == expected_type:
            return message

        if isinstance(message, CallbackMessage):
            run_callback(message)
            return expect_message(expected_type)

        if (
            isinstance(message, (CallbackResultMessage, CallbackErrorMessage))
            and (
                (expected_is_type and not isinstance(message, expected_type))
                or (not expected_is_type and message != expected_type)
            )
        ):
            return expect_message(expected_type)

        on_desync(message, expected_type)

    def run_callback(message):
        call_callback = system.gate.apply_with("internal", functional.call)
        try:
            result = call_callback(message.fn, message.args, message.kwargs)
        except Exception as exc:
            if debug:
                checkpoint(_on_error(exc))
            return None
        if debug:
            checkpoint(_on_result(result))
        return result

    def run_raw_callback(message):
        call_callback = system.gate.apply_with("internal", functional.call)
        try:
            result = call_callback(
                tape_reader.resolve(message.fn),
                tape_reader.resolve(message.args),
                tape_reader.resolve(message.kwargs),
            )
        except Exception as exc:
            if debug:
                raw_checkpoint(_on_error(exc))
            return None
        if debug:
            raw_checkpoint(_on_result(result))
        return result

    def consume_callback_completion():
        try:
            next_value = tape_reader._peek_item(resolve=False)
        except StopIteration:
            return False

        if next_value not in ("CALLBACK_RESULT", "CALLBACK_ERROR", "CHECKPOINT"):
            return False

        message = read_raw_message()
        return isinstance(
            message,
            (CallbackResultMessage, CallbackErrorMessage, CheckpointMessage),
        )

    def bind_replay_object(obj, on_callback):
        pending_callback_completions = 0

        while True:
            try:
                result = tape_reader.bind(obj)
                while pending_callback_completions:
                    if not consume_callback_completion():
                        break
                    pending_callback_completions -= 1
                return result
            except ExpectedBindMarker:
                message = read_raw_message()

                if isinstance(message, CallbackMessage):
                    pending_callback_completions += 1
                    on_callback(message)
                    continue

                if isinstance(
                    message,
                    (
                        CallbackResultMessage,
                        CallbackErrorMessage,
                        CallMarkerMessage,
                        ResultMessage,
                        ErrorMessage,
                        CheckpointMessage,
                    ),
                ):
                    continue

                return on_unexpected(message)
            
    # safeequal = system.disable_for(equal)
    def diff(record, replay):
        if not equal(record, replay):
            on_desync(record, replay)

    def checkpoint(replay):
        message = expect_message(CheckpointMessage)
        diff(record = message.value, replay = replay)

    def raw_checkpoint(replay):
        message = read_raw_message()
        if isinstance(message, CheckpointMessage):
            diff(record=tape_reader.resolve(message.value), replay=replay)
            return
        on_desync(message, CheckpointMessage)

    in_sandbox = system.enabled

    call = functional.sequence(
        _on_call, 
        checkpoint) if debug else functional.repeatedly(expect_message, CallMarkerMessage)

    if stacktraces:
        def next_stacktrace(*args, **kwargs):
            if in_sandbox():
                on_stacktrace()

        stacktrace_call = system.disable_for(next_stacktrace)
        checkpoint_call = call

        def call(*args, **kwargs):
            stacktrace_call(*args, **kwargs)
            return checkpoint_call(*args, **kwargs)

    system.wrap_async(utils.create_stub_object)
    system.wrap_async(gc.collect)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system.enabled),
        checkpoint, utils.noop)
    system.sync = system.disable_for(
        functional.repeatedly(expect_message, "SYNC"),
        unwrap_args=False,
    )

    system.primary_hooks = CallHooks(
        on_call=None,
        on_result=None,
        on_error=None,
    )

    def bind_internal_patched(obj):
        result = bind_replay_object(obj, run_raw_callback)
        system.is_bound.add(obj)
        return result

    system._on_alloc = system.create_dispatch(
        disabled=utils.noop,
        external=system._call_async_new_patched,
        internal=bind_internal_patched,
    )

    def bind_new_patched(obj):
        result = bind_replay_object(obj, utils.noop)
        system.is_bound.add(obj)
        return result

    system.async_new_patched = bind_new_patched

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
        try:
            tape_reader.consume_pending_closes(
                ignore_end_of_stream=True,
                buffered_only=True,
            )
        except StopIteration:
            return

    system.lifecycle_hooks=LifecycleHooks(
        on_start=on_start,
        on_end=on_end
        # on_end=functional.repeatedly(expect, "ON_END"),
        )

    @utils.exclude_from_stacktrace
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

            return on_unexpected(message)

    @utils.exclude_from_stacktrace
    def ext_execute(fn, *args, **kwargs):
        return next_result_message()

    system.ext_gateway_factory = functional.partial(ext_replay_gateway, ext_execute)
    system.int_gateway_factory = int_replay_gateway

    # system.ext_execute = ext_execute
    
    return system
