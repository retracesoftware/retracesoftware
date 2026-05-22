"""IO bridge helpers for the gate-based ``System``."""
from contextlib import contextmanager
import enum
import functools
import _thread
import _signal
import threading
import retracesoftware.functional as functional
import retracesoftware.stream as stream
import retracesoftware.utils as utils
from retracesoftware.install.monitoring import (
    begin_suppress_monitoring,
    end_suppress_monitoring,
)

from retracesoftware.proxy.messagestream import (
    BindingStream,
    ExpectedBindMarker,
    MessageStream,
    PeekableStream,
    SchedulerStream,
)
from retracesoftware.proxy.traceio import (
    CallMarkerMessage,
    CallbackErrorMessage,
    CallbackMessage,
    CallbackResultMessage,
    CheckpointMessage,
    ErrorMessage,
    OnStartMessage,
    ResultMessage,
    StacktraceMessage,
    SyncMessage,
    TraceWriter,
)

from retracesoftware.proxy.system import (
    CallHooks,
    LifecycleHooks,
    ProxyRef,
    RecordSystem,
    ReplaySystem,
    System,
)
import gc
import signal
import sys
import os
import types
from retracesoftware.proxy.gateway import (
    ext_replay_gateway,
    ext_replay_method_gateway,
    int_replay_gateway,
)

_THREAD_SCHEDULE_DEBUG = bool(os.environ.get("RETRACE_THREAD_SCHEDULE_DEBUG"))


def _debug_thread_schedule(message):
    if _THREAD_SCHEDULE_DEBUG:
        print(f"Retrace scheduler: {message}", file=sys.stderr, flush=True)


class _RetraceSpaceCallbacks:
    def __init__(self, callbacks, space):
        self._callbacks = callbacks
        self._space = space

    def _get(self):
        try:
            import _retrace
        except ImportError:
            return None
        getter = getattr(_retrace, "get_thread_switch_callback", None)
        if getter is None:
            return None
        return getter(self._space.id)

    def _set(self, callback):
        setter = getattr(self._callbacks, "set_thread_switch", None)
        if setter is None:
            self._callbacks.thread_switch = callback
            return
        setter(callback, self._space)

    @property
    def thread_switch(self):
        return self._get()

    @thread_switch.setter
    def thread_switch(self, callback):
        self._set(callback)


class _RetraceSpaceProbe:
    def __init__(self, probe, space):
        self._probe = probe
        self._space = space
        self.callbacks = _RetraceSpaceCallbacks(probe.callbacks, space)
        self.ThreadHandoff = probe.ThreadHandoff
        self.call_at = space.call_at
        self.coordinates = space.coordinates
        self.thread_delta = space.thread_delta
        self.exclude = getattr(probe, "exclude", None)
        self.disable = getattr(probe, "disable", None)


def _load_retrace_probe(retrace_space=None):
    try:
        import retrace as probe
    except ImportError:
        return None

    required = (
        "callbacks",
        "call_at",
        "thread_delta",
    )
    if all(hasattr(probe, name) for name in required):
        if retrace_space is not None:
            return _RetraceSpaceProbe(probe, retrace_space)
        return probe
    return None


def _load_retrace_probe_for_space(retrace_space):
    if retrace_space is None:
        return _load_retrace_probe()
    return _load_retrace_probe(retrace_space)


def _get_thread_switch_callback(probe):
    previous_count = begin_suppress_monitoring()
    try:
        callbacks = getattr(probe, "callbacks", None)
        if callbacks is not None:
            return getattr(callbacks, "thread_switch", None)
        getter = getattr(probe, "get_thread_switch_callback", None)
        if getter is None:
            return None
        return getter()
    finally:
        end_suppress_monitoring(previous_count)


def _set_thread_switch_callback(probe, callback):
    previous_count = begin_suppress_monitoring()
    try:
        callbacks = getattr(probe, "callbacks", None)
        if callbacks is not None:
            if not hasattr(callbacks, "thread_switch") and not hasattr(
                callbacks,
                "set_thread_switch",
            ):
                return False
            callbacks.thread_switch = callback
            return True
        setter = getattr(probe, "set_thread_switch_callback", None)
        if setter is None:
            return False
        setter(callback)
        return True
    finally:
        end_suppress_monitoring(previous_count)


def _retrace_include(probe, function):
    include = getattr(probe, "include", None)
    if include is None:
        return function
    return include(function)


def _in_disabled_method_wrapper():
    frame = sys._getframe()
    while frame is not None:
        if (
            frame.f_code.co_name == "wrapper"
            and frame.f_globals.get("__name__") == "retracesoftware.proxy.system"
        ):
            return True
        frame = frame.f_back
    return False


def _retrace_thread_switch_callback_hooks(system, trace_writer, *, retrace_space=None):
    probe = _load_retrace_probe_for_space(retrace_space)
    if probe is None:
        return None, None

    lock = threading.Lock()
    active_count = 0
    old_switch_callback = None
    last_thread_id = None

    def current_thread_enabled():
        return active_count > 0 or system.enabled() or _in_disabled_method_wrapper()

    def thread_in_retrace_space(thread_id):
        if thread_id is None or not hasattr(probe, "coordinates"):
            return True
        try:
            probe.coordinates(thread_id)
        except (LookupError, ValueError):
            return False
        return True

    def write_observed_thread_switch(next_thread_id, previous_delta):
        nonlocal last_thread_id
        if not thread_in_retrace_space(next_thread_id):
            return
        _debug_thread_schedule(
            f"record switch next={next_thread_id} delta={tuple(previous_delta)!r}"
        )
        trace_writer.thread_switch(tuple(previous_delta), next_thread_id)
        last_thread_id = next_thread_id

    def write_thread_switch(previous_delta, next_thread_id):
        if not current_thread_enabled():
            return None
        with lock:
            write_observed_thread_switch(next_thread_id, previous_delta)
        return None

    def install_callbacks(*_args, **_kwargs):
        nonlocal active_count, old_switch_callback, last_thread_id
        with lock:
            if active_count == 0:
                last_thread_id = system.thread_id()
                old_switch_callback = _get_thread_switch_callback(probe)
                _set_thread_switch_callback(probe, write_thread_switch)
            active_count += 1

    def uninstall_callbacks(*_args, **_kwargs):
        nonlocal active_count
        with lock:
            if active_count == 0:
                return
            active_count -= 1
            if active_count == 0:
                _set_thread_switch_callback(probe, old_switch_callback)

    return install_callbacks, uninstall_callbacks


def _retrace_gc_callback_hooks(
    system,
    trace_writer,
    write_callback_result,
    *,
    retrace_space=None,
):
    probe = _load_retrace_probe_for_space(retrace_space)
    if probe is None or not hasattr(probe, "coordinates"):
        return None, None

    active = False

    def write_thread_switch():
        # Keep retrace-python's delta state current, but record the interrupted
        # application cursor rather than this gc.callbacks frame.
        probe.thread_delta()
        cursor = tuple(probe.coordinates())[:-1]
        trace_writer.thread_switch((0, *cursor), system.thread_id())

    def on_gc(phase, info):
        nonlocal active

        if phase == "start":
            if active:
                return
            active = True
            write_thread_switch()
            system.write_callback(gc.collect, info.get("generation", 2))
            return

        if phase == "stop" and active:
            try:
                result = info.get("collected", 0) + info.get("uncollectable", 0)
                write_callback_result(result)
            finally:
                active = False

    def install_callbacks():
        gc.callbacks.append(on_gc)

    def uninstall_callbacks():
        try:
            gc.callbacks.remove(on_gc)
        except ValueError:
            pass

    return install_callbacks, uninstall_callbacks


def _retrace_signal_callback_hooks(
    system,
    trace_writer,
    write_callback_result,
    write_callback_error,
    *,
    retrace_space=None,
):
    probe = _load_retrace_probe_for_space(retrace_space)
    if probe is None or not hasattr(probe, "coordinates"):
        return None, None

    old_signal = _signal.signal
    wrapped_handlers = {}
    active = False

    def write_thread_switch():
        probe.thread_delta()
        cursor = tuple(probe.coordinates())[:-1]
        trace_writer.thread_switch((0, *cursor), system.thread_id())

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
            write_thread_switch()
            system.write_callback(handler, signum, None)
            try:
                result = handler(signum, frame)
            except BaseException:
                write_callback_error(*sys.exc_info())
                raise
            else:
                write_callback_result(result)
                return result
            finally:
                active = False

        wrapped_handlers[handler] = signal_handler
        return signal_handler

    def signal_signal(signum, handler):
        return old_signal(signum, wrap_handler(handler))

    def install_callbacks():
        _signal.signal = signal_signal

    def uninstall_callbacks():
        _signal.signal = old_signal

    return install_callbacks, uninstall_callbacks


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

    args_a = a.get("args", ())
    args_b = b.get("args", ())
    kwargs_a = a.get("kwargs", {})
    kwargs_b = b.get("kwargs", {})

    if not equal(fn_a, fn_b) and not same_dynamic_proxy_shim:
        return False

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
    if _is_checkpoint_sync_primitive_marker(a):
        return _checkpoint_sync_primitive_marker_matches_value(a, b)
    if _is_checkpoint_sync_primitive_marker(b):
        return _checkpoint_sync_primitive_marker_matches_value(b, a)
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
_CHECKPOINT_SYNC_PRIMITIVE_MARKER = "__retrace_checkpoint_sync_primitive__"
_CHECKPOINT_TRACEBACK_MARKER = "__retrace_checkpoint_traceback__"

_CHECKPOINT_SYNC_PRIMITIVE_TYPES = frozenset(
    {
        ("_thread", "lock"),
        ("_thread", "RLock"),
        ("threading", "Barrier"),
        ("threading", "BoundedSemaphore"),
        ("threading", "Condition"),
        ("threading", "Event"),
        ("threading", "Semaphore"),
    }
)


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


def _checkpoint_sync_primitive_type_key(value):
    cls = type(value)
    type_key = (
        getattr(cls, "__module__", ""),
        getattr(cls, "__qualname__", getattr(cls, "__name__", "")),
    )
    if type_key in _CHECKPOINT_SYNC_PRIMITIVE_TYPES:
        return type_key
    return None


def _checkpoint_sync_primitive_marker(value):
    type_key = _checkpoint_sync_primitive_type_key(value)
    if type_key is None:
        return value
    return (_CHECKPOINT_SYNC_PRIMITIVE_MARKER, *type_key)


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
        _checkpoint_sync_primitive_marker,
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


def _is_checkpoint_sync_primitive_marker(value):
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and value[0] == _CHECKPOINT_SYNC_PRIMITIVE_MARKER
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


def _checkpoint_sync_primitive_marker_matches_value(marker, value):
    if _is_checkpoint_sync_primitive_marker(value):
        return marker == value
    return marker[1:] == _checkpoint_sync_primitive_type_key(value)


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

def call_recorder(
    *,
    writer: TraceWriter,
    debug: bool = False,
    stacktraces: bool = False,
    retrace_space=None,
) -> System:
    binder = stream.Binder(
        on_delete=functional.sequence(_binding_handle, writer.binding_delete)
    )
    def bind_for_record(obj):
        binder.autobind(obj)
        writer.new_binding(_binding_handle(binder.lookup(obj)))

    system = RecordSystem(
        writer=writer,
        bind=bind_for_record,
        internal_space=retrace_space,
    )

    encode_trace_value = functional.walker(binder)

    def binding_writer(write_message):
        def write_bound_message(value):
            return write_message(encode_trace_value(value))
        return write_bound_message

    def write_checkpoint(value):
        return writer.checkpoint(
            system.internal_space.thread_delta(),
            _thread.get_ident(),
            encode_trace_value(value),
        )

    def write_callback(fn, *args, **kwargs):
        writer.callback(
            encode_trace_value(fn),
            encode_trace_value(args),
            encode_trace_value(kwargs),
        )

    in_sandbox = system.enabled

    on_start = functional.repeatedly(writer.on_start)
    on_end = None

    checkpoint = functional.sequence(
        functional.walker(_checkpoint_stable_marker),
        write_checkpoint,
    )

    call = functional.sequence(_on_call, checkpoint) if debug else None

    if stacktraces:
        stack = utils.StackFactory()
        stacktrace = writer.stacktrace

        write_stacktrace = functional.repeatedly(functional.if_then_else(
            lambda: in_sandbox(),
            functional.sequence(stack.delta, normalize_stack_delta, stacktrace),
            utils.noop))

        stacktrace_call = system.disable_for(write_stacktrace)
        call = utils.runall(stacktrace_call, call) if call is not None else stacktrace_call
        on_start = functional.sequence(functional.repeatedly(stack.delta), on_start)

    def get_error(*funcs):
        return functional.sequence(functional.positional_param(1), *funcs)

    on_callback_result = functional.sequence(_on_result, checkpoint) \
        if debug else binding_writer(writer.callback_result)

    on_callback_error = get_error(_on_error, checkpoint) \
        if debug else writer.callback_error

    create_stub_object = system.wrap_async(utils.create_stub_object)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system.enabled),
        checkpoint, utils.noop)

    system.lifecycle_hooks = LifecycleHooks(
            on_start = on_start,
            on_end = on_end,
            #functional.repeatedly(write, "ON_END")
            )
            
    system.write_callback = write_callback
    system.write_callback_result = on_callback_result
    system.write_callback_error = on_callback_error

    on_call = write_callback

    on_result = functional.sequence(
        encode_trace_value, writer.result
    )

    on_error = functional.sequence(
        functional.positional_param(1), 
        functional.if_then_else(
            functional.isinstanceof(Exception),
            writer.error,
            utils.noop))

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
        system.write_callback(create_stub_object, cls)
        system.bind(obj)
        system.write_callback_result(obj)

    system.async_new_patched = async_new_patched

    system.passthrough_proxyref = True

    return system


def recorder(
    *,
    writer: TraceWriter,
    debug: bool = False,
    stacktraces: bool = False,
    retrace_space=None,
) -> System:
    system = call_recorder(
        writer=writer,
        debug=debug,
        stacktraces=stacktraces,
        retrace_space=retrace_space,
    )

    on_start = system.lifecycle_hooks.on_start
    on_end = system.lifecycle_hooks.on_end

    (
        install_thread_switch_callbacks,
        uninstall_thread_switch_callbacks,
    ) = (
        _retrace_thread_switch_callback_hooks(
            system,
            writer,
            retrace_space=retrace_space,
        )
    )
    if install_thread_switch_callbacks is not None:
        on_start = utils.runall(on_start, install_thread_switch_callbacks)
        on_end = utils.runall(uninstall_thread_switch_callbacks, on_end)

    install_gc_callbacks, uninstall_gc_callbacks = _retrace_gc_callback_hooks(
        system,
        writer,
        system.write_callback_result,
        retrace_space=retrace_space,
    )
    if install_gc_callbacks is not None:
        on_start = utils.runall(on_start, install_gc_callbacks)
        on_end = utils.runall(uninstall_gc_callbacks, on_end)

    install_signal_callbacks, uninstall_signal_callbacks = _retrace_signal_callback_hooks(
        system,
        writer,
        system.write_callback_result,
        system.write_callback_error,
        retrace_space=retrace_space,
    )
    if install_signal_callbacks is not None:
        on_start = utils.runall(on_start, install_signal_callbacks)
        on_end = utils.runall(uninstall_signal_callbacks, on_end)

    system.lifecycle_hooks = LifecycleHooks(
        on_start=on_start,
        on_end=on_end,
    )

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
             stacktraces: bool = False,
             retrace_space=None) -> System:
    current_thread_id = _thread.get_ident
    replay_probe = _load_retrace_probe_for_space(retrace_space)
    handoff = (
        replay_probe.ThreadHandoff()
        if replay_probe is not None and hasattr(replay_probe, "ThreadHandoff")
        else None
    )

    raw_messages = PeekableStream(MessageStream(next_object, close=close))
    thread_source = SchedulerStream(
        raw_messages,
        probe=replay_probe,
        handoff=handoff,
        initial_thread_id=None,
        current_thread_id=current_thread_id,
        close=raw_messages.close,
        active=False,
    )
    scheduled_messages = PeekableStream(thread_source)
    tape_reader = BindingStream(scheduled_messages)

    read_message = tape_reader.next
    peek_message = tape_reader.peek
    system = ReplaySystem(bind=tape_reader.bind, internal_space=retrace_space)
    binding_depth = 0
    replay_schedule_defer_depth = 0
    replay_result_depth = 0
    if handoff is not None:
        system.is_bound.add(handoff)
    thread_source.set_disable_for(system.disable_for)

    def should_replay_thread_schedule():
        if binding_depth:
            return False
        if replay_result_depth:
            return True
        return system.enabled() or _in_disabled_method_wrapper()

    thread_source.set_replay_guards(should_schedule=should_replay_thread_schedule)

    @contextmanager
    def defer_replay_thread_schedule():
        nonlocal replay_schedule_defer_depth
        replay_schedule_defer_depth += 1
        try:
            yield
        finally:
            replay_schedule_defer_depth -= 1

    system.defer_replay_thread_schedule = defer_replay_thread_schedule
    system.handoff_replay_thread_schedule_to = (
        thread_source.handoff_thread_schedule_to
    )

    on_unexpected = system.disable_for(on_unexpected, unwrap_args=False)
    on_desync = system.disable_for(on_desync, unwrap_args=False)
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
        call_callback = system.apply_with("internal", functional.call)
        try:
            fn = _retrace_include(replay_probe, message.fn)
            return call_callback(fn, message.args, message.kwargs)
        except Exception as exc:
            return None

    def run_raw_callback(message):
        return run_callback(message)

    def is_callback_checkpoint(message):
        if not isinstance(message, CheckpointMessage):
            return False
        value = message.value
        return (
            isinstance(value, dict)
            and len(value) == 1
            and ("result" in value or "error" in value)
        )

    def consume_callback_completion():
        try:
            message = peek_message()
        except StopIteration:
            return False

        if not isinstance(
            message,
            (CallbackResultMessage, CallbackErrorMessage, CheckpointMessage),
        ):
            return False
        if isinstance(message, CheckpointMessage) and not is_callback_checkpoint(message):
            return False

        read_message()
        return isinstance(
            message,
            (CallbackResultMessage, CallbackErrorMessage, CheckpointMessage),
        )

    def bind_replay_object(obj, on_callback):
        nonlocal binding_depth
        pending_callback_completions = 0

        binding_depth += 1
        try:
            while True:
                try:
                    result = tape_reader.bind(obj)
                    while pending_callback_completions:
                        if not consume_callback_completion():
                            break
                        pending_callback_completions -= 1
                    return result
                except ExpectedBindMarker:
                    message = read_message()

                    if isinstance(message, CallbackMessage):
                        pending_callback_completions += 1
                        on_callback(message)
                        continue

                    if isinstance(
                        message,
                        (
                            CallbackResultMessage,
                            CallbackErrorMessage,
                            ResultMessage,
                            ErrorMessage,
                        ),
                    ) or is_callback_checkpoint(message):
                        continue

                    return on_unexpected(message)
        finally:
            binding_depth -= 1
            
    # safeequal = system.disable_for(equal)
    def diff(record, replay):
        if not equal(record, replay):
            on_desync(record, replay)

    def diff_thread(message):
        if message.thread_id != _thread.get_ident():
            on_desync(message.thread_id, _thread.get_ident())

    def checkpoint(replay):
        message = expect_message(CheckpointMessage)
        diff_thread(message)
        diff(record = message.value, replay = replay)

    def raw_checkpoint(replay):
        message = read_message()
        if isinstance(message, CheckpointMessage):
            diff_thread(message)
            diff(record=message.value, replay=replay)
            return
        on_desync(message, CheckpointMessage)

    def sync():
        message = read_message()
        if isinstance(message, SyncMessage):
            return None
        return on_unexpected(message)

    def monitor_checkpoint(value):
        message = read_message()
        if message != "MONITOR":
            raise RuntimeError(f"expected 'MONITOR', got {message!r}")
        recorded = read_message()
        if recorded != value:
            raise RuntimeError(
                f"monitor divergence: recorded {recorded!r}, replayed {value!r}"
            )

    in_sandbox = system.enabled

    call = functional.sequence(_on_call, checkpoint) if debug else None

    if stacktraces:
        def next_stacktrace(*args, **kwargs):
            if in_sandbox():
                on_stacktrace()

        stacktrace_call = system.disable_for(next_stacktrace)
        call = utils.runall(stacktrace_call, call) if call is not None else stacktrace_call

    system.wrap_async(utils.create_stub_object)

    system.checkpoint = functional.if_then_else(
        functional.repeatedly(system.enabled),
        checkpoint, utils.noop)

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
    system.sync = sync
    system.monitor_checkpoint = monitor_checkpoint

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
        thread_source.activate()

    def on_end():
        thread_source.deactivate()
        try:
            tape_reader.consume_pending_closes(
                ignore_end_of_stream=True,
                buffered_only=True,
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, (StopIteration, RuntimeError)):
                return
            raise

    system.lifecycle_hooks=LifecycleHooks(
        on_start=on_start,
        on_end=on_end
        # on_end=functional.repeatedly(expect, "ON_END"),
        )

    @utils.exclude_from_stacktrace
    def next_result_message():
        while True:
            tape_reader.consume_pending_closes()
            if isinstance(peek_message(), SyncMessage):
                raise KeyboardInterrupt() from None

            message = read_message()

            if isinstance(message, CallbackMessage):
                run_callback(message)
                continue

            if isinstance(message, (CallbackResultMessage, CallbackErrorMessage)):
                continue

            if is_callback_checkpoint(message):
                continue

            if isinstance(message, CallMarkerMessage):
                continue

            if isinstance(message, ResultMessage):
                result = message.result
                if not replay_schedule_defer_depth:
                    thread_source.advance_thread_schedule(
                        skip_handoff_if_current_done=True,
                    )
                return result

            if isinstance(message, ErrorMessage):
                error = message.error
                if not replay_schedule_defer_depth:
                    thread_source.advance_thread_schedule(
                        skip_handoff_if_current_done=True,
                    )
                raise error

            return on_unexpected(message)

    @utils.exclude_from_stacktrace
    def ext_execute(*args, **kwargs):
        nonlocal replay_result_depth
        replay_result_depth += 1
        try:
            return next_result_message()
        finally:
            replay_result_depth -= 1

    system.next_result = system.disable_for(ext_execute, unwrap_args=False)
    
    return system
