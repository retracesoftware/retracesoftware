"""
retracesoftware.stream - Runtime selectable release/debug builds

Set RETRACE_DEBUG=1 to use the debug build with symbols and assertions.
"""
import base64
import json
import os
import pickle
import threading
import time
import weakref
import sys
from types import ModuleType
from typing import Any

import retracesoftware.functional as functional
import retracesoftware.utils as utils


def _is_truthy_env(v):
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}

_DEBUG_MODE = _is_truthy_env(os.getenv("RETRACE_DEBUG"))

_backend_mod: ModuleType
__backend__: str

try:
    if _DEBUG_MODE:
        import _retracesoftware_stream_debug as _backend_mod  # type: ignore
        __backend__ = "native-debug"
    else:
        import _retracesoftware_stream_release as _backend_mod  # type: ignore
        __backend__ = "native-release"
except Exception as e:
    raise ImportError(f"Failed to load retracesoftware_stream native extension: {e}") from e

# Expose debug mode flag
DEBUG_MODE = _DEBUG_MODE and __backend__.startswith("native")

def __getattr__(name: str) -> Any:
    return getattr(_backend_mod, name)

def _export_public(mod: ModuleType) -> None:
    g = globals()
    for k, v in mod.__dict__.items():
        if k.startswith("_"):
            continue
        g[k] = v

_export_public(_backend_mod)

_NATIVE_PERSISTER_TYPES = tuple(
    t for t in (
        getattr(_backend_mod, "Persister", None),
    )
    if t is not None
)


def _dispatch_debug_handler(handler, event):
    if callable(handler):
        return handler(event)
    return handler.handle_event(event)


def _debug_event(tag, payload):
    return (tag, payload)


def _debug_object_event(obj):
    return _debug_event("object", obj)


def _debug_command_event(name, args=()):
    return _debug_event("command", (name, args))


class _StallTimeoutBackoff:
    def __init__(self, stall_timeout, retry_delay=0.001, reset_window=0.05):
        self.stall_timeout = float(stall_timeout)
        self.retry_delay = float(retry_delay)
        self.reset_window = float(reset_window)
        self._started_at = None
        self._last_called_at = None

    def __call__(self):
        now = time.monotonic()
        if self._last_called_at is None or now - self._last_called_at > self.reset_window:
            self._started_at = now
        self._last_called_at = now

        if self.stall_timeout <= 0:
            return None

        elapsed = now - self._started_at
        remaining = self.stall_timeout - elapsed
        if remaining <= 0:
            return None
        return min(self.retry_delay, remaining)


class DebugPersister:
    def __init__(self, handler, quit_on_error=False):
        if not callable(handler) and not hasattr(handler, "handle_event"):
            raise TypeError(
                "DebugPersister handler must be callable or define handle_event(event)"
            )
        self.handler = handler
        self.quit_on_error = bool(quit_on_error)
        self.reset_state()

    def _dispatch_event(self, event):
        try:
            return _dispatch_debug_handler(self.handler, event)
        except Exception:
            import traceback

            if self.quit_on_error:
                print("retrace: python persister callback error (quit_on_error is set)", file=sys.stderr)
                traceback.print_exc()
                os._exit(1)
            traceback.print_exc()
            return None

    def _index_event(self, name, value):
        return self._dispatch_event(_debug_event(name, value))

    def _command0(self, name):
        return self._dispatch_event(_debug_command_event(name))

    def _command1(self, name, value):
        return self._dispatch_event(_debug_command_event(name, (value,)))

    def _handle_index(self, ref_id):
        index = self._handle_indices.get(ref_id)
        if index is not None:
            return index
        index = self._next_handle_index
        self._handle_indices[ref_id] = index
        self._next_handle_index += 1
        return index

    def _handle_delete_delta(self, ref_id):
        index = self._handle_indices.pop(ref_id, None)
        if index is None:
            return None
        return self._next_handle_index - index - 1

    def _bind_index(self, ref_id):
        return self._bindings.get(ref_id, self._next_binding_index)

    def _remember_binding(self, ref_id, index, obj=None):
        self._bindings[ref_id] = index
        if obj is not None:
            self._binding_values[index] = obj
        self._next_binding_index = max(self._next_binding_index, index + 1)

    def _bound_ref_index(self, obj):
        return self._bindings.get(id(obj))

    def reset_state(self):
        self._handle_indices = {}
        self._next_handle_index = 0
        self._bindings = {}
        self._binding_values = {}
        self._next_binding_index = 0

    @staticmethod
    def _ref_key(ref):
        return id(ref)

    def write_object(self, obj):
        index = self._bound_ref_index(obj)
        if index is not None:
            return self._index_event("bound_ref", index)
        return self._dispatch_event(_debug_object_event(obj))

    def write_handle_ref(self, ref):
        return self._index_event("handle_ref", self._handle_index(self._ref_key(ref)))

    def write_handle_delete(self, ref):
        delta = self._handle_delete_delta(self._ref_key(ref))
        if delta is None:
            return None
        return self._index_event("handle_delete", delta)

    def intern(self, obj, ref):
        ref_key = self._ref_key(ref)
        index = self._bind_index(ref_key)
        self._remember_binding(ref_key, index, obj)
        return self._dispatch_event(
            _debug_command_event("intern", (index, _debug_object_event(obj)))
        )

    def write_bound_ref_delete(self, ref):
        index = self._bindings.pop(self._ref_key(ref), None)
        if index is None:
            return None
        self._binding_values.pop(index, None)
        return self._index_event("bound_ref_delete", index)

    def flush(self):
        return self._command0("flush")

    def shutdown(self):
        return self._command0("shutdown")

    def start_collection(self, typ, length):
        if typ is list:
            return self._command1("list", length)
        if typ is tuple:
            return self._command1("tuple", length)
        if typ is dict:
            return self._command1("dict", length)
        raise ValueError(f"unknown collection type: {typ!r}")

    def write_heartbeat(self):
        return self._command0("heartbeat")

    def write_delete(self, ref):
        return self._command1("delete", self._ref_key(ref))

    def write_thread_switch(self, thread_handle):
        return self._command1("thread_switch", thread_handle)

    def write_pickled(self, obj):
        return self._dispatch_event(
            _debug_command_event("pickled", (_debug_object_event(obj),))
        )

    def write_new_handle(self, ref, obj):
        index = self._handle_index(self._ref_key(ref))
        return self._dispatch_event(
            _debug_command_event("new_handle", (index, _debug_object_event(obj)))
        )

    def write_new_patched(self, typ_ref, ref):
        type_index = self._bindings[self._ref_key(typ_ref)]
        typ = self._binding_values.get(type_index, typ_ref)
        ref_key = self._ref_key(ref)
        self._remember_binding(ref_key, self._bind_index(ref_key))
        return self._dispatch_event(
            _debug_command_event(
                "new_patched",
                (_debug_object_event(typ),),
            )
        )

    def bind(self, ref):
        ref_key = self._ref_key(ref)
        index = self._bind_index(ref_key)
        self._remember_binding(ref_key, index)
        return self._command1("bind", index)

    def write_serialize_error(self):
        return self._command0("serialize_error")


def _qualified_name(obj):
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", getattr(obj, "__name__", type(obj).__name__))
    if module and module != "builtins":
        return f"{module}.{qualname}"
    return qualname


def _encode_bytes_payload(obj):
    return {
        "kind": "bytes",
        "encoding": "base64",
        "data": base64.b64encode(bytes(obj)).decode("ascii"),
    }


class JsonPersister:
    def __init__(self, path_or_stream, serializer=None, preamble=None, quit_on_error=False):
        self._serializer = serializer
        self.quit_on_error = bool(quit_on_error)
        self._owns_stream = not hasattr(path_or_stream, "write")
        if self._owns_stream:
            self.path = str(path_or_stream)
            self._stream = open(self.path, "a", encoding="utf-8")
        else:
            self._stream = path_or_stream
            self.path = getattr(path_or_stream, "name", "")
        self.reset_state()
        if preamble is not None:
            self._write_event("process_info", value=self._encode_value(preamble, use_serializer=False))

    def _write_event(self, event, **payload):
        self._stream.write(json.dumps({"event": event, **payload}, sort_keys=True) + "\n")

    def _bind_index(self, ref_id):
        return self._bindings.get(ref_id, self._next_binding_index)

    def _remember_binding(self, ref_id, index, obj=None):
        self._bindings[ref_id] = index
        if obj is not None:
            self._binding_values[index] = obj
        self._next_binding_index = max(self._next_binding_index, index + 1)

    def _forget_binding(self, ref_id):
        index = self._bindings.pop(ref_id, None)
        if index is not None:
            self._binding_values.pop(index, None)
        return index

    def _bound_ref_index(self, obj):
        return self._bindings.get(id(obj))

    def _encode_value(self, value, use_serializer=True):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return _encode_bytes_payload(value)
        if isinstance(value, list):
            return [self._encode_value(item, use_serializer=False) for item in value]
        if isinstance(value, tuple):
            return {
                "kind": "tuple",
                "items": [self._encode_value(item, use_serializer=False) for item in value],
            }
        if isinstance(value, dict):
            return {
                "kind": "dict",
                "items": [
                    [
                        self._encode_value(key, use_serializer=False),
                        self._encode_value(item, use_serializer=False),
                    ]
                    for key, item in value.items()
                ],
            }
        if isinstance(value, BaseException):
            return {
                "kind": "exception",
                "type": _qualified_name(type(value)),
                "args": [self._encode_value(arg, use_serializer=False) for arg in value.args],
                "repr": repr(value),
            }
        if isinstance(value, type):
            return {"kind": "type", "name": _qualified_name(value)}
        if use_serializer and self._serializer is not None:
            try:
                serialized = self._serializer(value)
            except Exception:
                serialized = None
            else:
                if isinstance(serialized, (bytes, bytearray, memoryview)):
                    return {
                        "kind": "pickled",
                        "encoding": "base64",
                        "data": base64.b64encode(bytes(serialized)).decode("ascii"),
                    }
                return {
                    "kind": "serialized",
                    "value": self._encode_value(serialized, use_serializer=False),
                }
        return {
            "kind": "repr",
            "type": _qualified_name(type(value)),
            "repr": repr(value),
        }

    def reset_state(self):
        self._bindings = {}
        self._binding_values = {}
        self._next_binding_index = 0

    def write_object(self, obj):
        index = self._bound_ref_index(obj)
        if index is not None:
            self._write_event("bound_ref", index=index)
            return None
        self._write_event("object", value=self._encode_value(obj))
        return None

    def write_delete(self, ref):
        ref_id = id(ref)
        self._write_event("delete", ref=ref_id, index=self._forget_binding(ref_id))
        return None

    def intern(self, obj, ref):
        ref_id = id(ref)
        index = self._bind_index(ref_id)
        self._remember_binding(ref_id, index, obj)
        self._write_event("intern", index=index, value=self._encode_value(obj))
        return None

    def write_new_patched(self, typ_ref, ref):
        type_index = self._bindings.get(id(typ_ref))
        typ = self._binding_values.get(type_index, typ_ref)
        ref_id = id(ref)
        index = self._bind_index(ref_id)
        self._remember_binding(ref_id, index)
        self._write_event(
            "new_patched",
            index=index,
            type_ref=type_index,
            type_value=self._encode_value(typ, use_serializer=False),
        )
        return None

    def start_collection(self, typ, length):
        self._write_event("start_collection", collection_type=typ.__name__, length=int(length))
        return None

    def flush(self):
        self._stream.flush()
        return None

    def flush_background(self):
        return self.flush()

    def shutdown(self):
        return self.flush()

    def prepare_resume(self):
        return self.flush()

    def write_thread_switch(self, thread_handle):
        self._write_event("thread_switch", value=self._encode_value(thread_handle, use_serializer=False))
        return None

    def write_heartbeat(self):
        self._write_event("heartbeat")
        return None

    def bind(self, ref):
        ref_id = id(ref)
        index = self._bind_index(ref_id)
        self._remember_binding(ref_id, index)
        self._write_event("bind", index=index)
        return None

    def write_pickled(self, obj):
        self._write_event("pickled", value=_encode_bytes_payload(obj))
        return None

    def write_serialize_error(self):
        self._write_event("serialize_error")
        return None

    def close(self):
        if self._stream is None:
            return
        self.flush()
        if self._owns_stream:
            self._stream.close()
        self._stream = None


class ObjectWriter:
    def __init__(self, queue, serializer=None, verbose=False, quit_on_error=False):
        self.type_serializer = {}
        self._queue = queue
        self._serializer = serializer
        self._bound = {}
        self._native = None

        if isinstance(queue, getattr(_backend_mod, "Queue")):
            self._native = _backend_mod.ObjectWriter(
                queue,
                utils.ExternalWrapped,
                verbose=verbose,
                quit_on_error=quit_on_error,
            )

    def __getattr__(self, name):
        if self._native is None:
            raise AttributeError(name)
        return getattr(self._native, name)

    def _bind_token(self, obj):
        token = self._bound.get(obj)
        if token is None:
            token = id(obj)
            self._bound[obj] = token
        return token

    def bind(self, obj):
        if self._native is not None:
            return self._native.bind(obj)
        self._queue.push_bind(self._bind_token(obj))
        return None

    def intern(self, obj):
        if self._native is not None:
            return self._native.intern(obj)
        token = self._bind_token(obj)
        self._queue.push_intern(obj, token)
        return None

    def new_patched(self, obj):
        if self._native is not None:
            return self._native.new_patched(obj)
        typ = type(obj)
        if typ not in self._bound:
            self.intern(typ)
        token = self._bind_token(obj)
        type_token = self._bind_token(typ)
        self._queue.push_new_patched(type_token, token)
        return None

# ---------------------------------------------------------------------------
# High-level API (convenience wrappers around C++ extension)
# ---------------------------------------------------------------------------

def call_periodically(interval, func):
    ref = weakref.ref(func)
    sleep = time.sleep

    def run():
        while True:
            obj = ref()
            if obj is None:
                break
            obj()
            del obj          # release strong ref before sleeping
            sleep(interval)

    threading.Thread(target=run, args=(), name="Retrace flush tracefile", daemon=True).start()

def get_path_info():
    """Get current path info for recording in settings.json."""
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:
        cwd = None
    
    real_sys_path = []
    for p in sys.path:
        if p:
            try:
                real_p = os.path.realpath(p)
                if real_p:
                    real_sys_path.append(real_p)
            except OSError:
                pass
    
    return {
        'cwd': cwd,
        'sys_path': real_sys_path,
    }


def _skip_shebang(f):
    """Skip past an optional '#!' shebang line. File position is left
    at the start of binary data (first PID frame or first unframed message)."""
    peek = f.read(2)
    if peek == b'#!':
        f.readline()
    elif peek:
        f.seek(-len(peek), 1)


def detect_raw_trace(path):
    """Return True for unframed traces, False for PID-framed traces."""
    with open(str(path), 'rb') as f:
        _skip_shebang(f)
        first = f.read(1)
    return first == b'{'


def list_pids(path):
    """Scan a PID-framed trace and return the set of unique PIDs."""
    pids = set()
    with open(str(path), 'rb') as f:
        _skip_shebang(f)
        while True:
            header = f.read(6)
            if len(header) < 6:
                break
            pid = int.from_bytes(header[:4], 'little')
            length = int.from_bytes(header[4:6], 'little')
            pids.add(pid)
            f.seek(length, 1)
    return pids


def _write_process_info(fw, info):
    """Write process info as a JSON line via a FramedWriter.

    The message-layer format is ``{json}\\n`` (no length prefix),
    transported inside PID-framed chunks.
    """
    import json

    info = {**info, 'encoding_version': 1}
    json_bytes = json.dumps(info, separators=(',', ':')).encode('utf-8')
    fw.write(json_bytes)
    fw.write(b'\n')
    fw.flush()


def read_process_info(path, raw=False):
    """Read JSON process info from the beginning of a trace file.

    When *raw* is False (default), the file is PID-framed and PID
    headers are parsed to reassemble the JSON payload. When *raw* is
    True the file is a plain (unframed) stream.

    In both cases the preamble is a single JSON line terminated by
    ``\\n`` (no length prefix).

    Returns ``(info_dict, byte_offset)`` where byte_offset is the file
    position immediately after the process info, suitable for passing
    as ``start_offset`` to the C++ reader.
    """
    import json

    with open(str(path), 'rb') as f:
        _skip_shebang(f)

        if raw:
            line = f.readline()
            if not line:
                raise ValueError("trace file is empty")
            info = json.loads(line)
            return info, f.tell()

        # PID-framed path: reassemble the process info payload from
        # PID frames.  The first frames belong to the main PID and
        # contain a JSON line (terminated by \n) which may span
        # multiple PID frames.
        header = f.read(6)
        if len(header) < 6:
            raise ValueError("trace file too short for process info")
        main_pid = int.from_bytes(header[:4], 'little')
        payload_len = int.from_bytes(header[4:6], 'little')
        data = f.read(payload_len)

        while b'\n' not in data:
            header = f.read(6)
            if len(header) < 6:
                raise ValueError("unexpected EOF while reading process info")
            pid = int.from_bytes(header[:4], 'little')
            plen = int.from_bytes(header[4:6], 'little')
            chunk = f.read(plen)
            if pid == main_pid:
                data += chunk

        file_offset = f.tell()

    json_bytes = data[:data.index(b'\n')]
    info = json.loads(json_bytes)
    return info, file_offset


class writer(_backend_mod.ObjectWriter):

    def handle(self, obj):
        self.intern(obj)
        return functional.partial(self, obj)

    def __init__(self, path=None, thread=None, output=None, queue=None,
                 format="binary",
                 flush_interval=0.1,
                 verbose=False,
                 disable_retrace=None,
                 preamble=None,
                 push_fail_callback=None,
                 on_consumer_error=None,
                 stall_timeout=None,
                 inflight_limit=None,
                 consumer_wait_timeout_ms=None,
                 queue_capacity=None,
                 quit_on_error=False,
                 serialize_errors=True,
                 validate_bindings=False):

        if format not in {"binary", "unframed_binary", "json"}:
            raise ValueError(f"unsupported writer format: {format!r}")

        self._fw = None
        self._close_output = False
        self._queue = queue
        effective_push_fail_callback = push_fail_callback
        if effective_push_fail_callback is None and stall_timeout is not None:
            effective_push_fail_callback = _StallTimeoutBackoff(stall_timeout)

        queue_kwargs = {}
        if inflight_limit is not None:
            queue_kwargs["inflight_limit"] = inflight_limit
        if consumer_wait_timeout_ms is not None:
            queue_kwargs["worker_wait_timeout_ms"] = consumer_wait_timeout_ms
        if queue_capacity is not None:
            queue_kwargs["queue_capacity"] = queue_capacity
        if thread is not None:
            if not callable(thread) and hasattr(thread, "get"):
                thread = thread.get
            queue_kwargs["thread"] = thread
        if effective_push_fail_callback is not None:
            queue_kwargs["push_fail_callback"] = effective_push_fail_callback
        if on_consumer_error is not None:
            queue_kwargs["on_target_error"] = on_consumer_error

        if self._queue is None and path is not None:
            if format in {"binary", "unframed_binary"}:
                fw = _backend_mod.FramedWriter(
                    str(path),
                    raw=(format == "unframed_binary"),
                )
                self._fw = fw

                if preamble is not None:
                    _write_process_info(fw, preamble)

                output = _backend_mod.Persister(
                    fw,
                    serializer=self.serialize,
                )
            else:
                output = JsonPersister(
                    path,
                    serializer=self.serialize,
                    preamble=preamble,
                    quit_on_error=quit_on_error,
                )
                self._close_output = True

        if self._queue is None and output is not None:
            self._queue = _backend_mod.Queue(output, **queue_kwargs)

        if self._queue is None:
            raise ValueError("writer requires a queue, output, or path")
        if inflight_limit is not None and self._queue is not None:
            self._queue.inflight_limit = inflight_limit
        if effective_push_fail_callback is not None and self._queue is not None:
            self._queue.push_fail_callback = effective_push_fail_callback
        if on_consumer_error is not None and self._queue is not None:
            self._queue.on_target_error = on_consumer_error

        self._output = output
        self._disable_retrace = disable_retrace
        self._heartbeat_enabled = True
        self._heartbeat_lock = threading.Lock()

        kwargs = dict(verbose=verbose)
        if quit_on_error:
            kwargs['quit_on_error'] = quit_on_error

        super().__init__(self._queue, utils.ExternalWrapped, **kwargs)

        if path is not None:
            self.path = path

        self.type_serializer = {}

        try:
            from retracesoftware.utils import Stack
            self.type_serializer[Stack] = tuple
        except ImportError:
            pass

        call_periodically(interval=flush_interval, func=self.heartbeat)

        if self._fw is not None and path is not None and hasattr(os, 'register_at_fork'):
            os.register_at_fork(
                before=self._before_fork,
                after_in_parent=self._after_fork_parent,
                after_in_child=self._after_fork_child,
            )

    def __enter__(self): return self

    def __exit__(self, *args):
        with self._heartbeat_lock:
            self._heartbeat_enabled = False
            self.flush()
            self.disable()
            if hasattr(self, '_queue') and self._queue and hasattr(self._queue, 'close'):
                self._queue.close()
            if hasattr(self, '_fw') and self._fw and hasattr(self._fw, 'close'):
                self._fw.close()
            if self._close_output and hasattr(self, '_output') and self._output and hasattr(self._output, 'close'):
                self._output.close()
            self._output = None
            self._queue = None
            self._fw = None

    def serialize(self, obj):
        serializer = self.type_serializer.get(type(obj))
        if serializer is not None:
            return serializer(obj)

        if utils.is_wrapped(obj):
            from retracesoftware.proxy.stubfactory import StubRef

            return StubRef(type(utils.unwrap(obj)))

        return pickle.dumps(obj)

    def heartbeat(self):
        with self._heartbeat_lock:
            if not getattr(self, "_heartbeat_enabled", False):
                return
            queue = getattr(self, "_queue", None)
            if queue is None:
                return
            # Heartbeats are best-effort diagnostics; skip them while the
            # writer is already under load to avoid competing with the main
            # producer thread during backpressure.
            if getattr(queue, "inflight_bytes", 0) > 0:
                return
            import resource
            payload = {
                'ts': time.time(),
                'inflight': queue.inflight_bytes,
                'messages': self.messages_written,
                'rss': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'threads': threading.active_count(),
            }
            super().heartbeat(payload)

    # -- Fork safety ----------------------------------------------------------
    # PID-framed writes (each <= PIPE_BUF) let parent and child share the
    # same fd after fork.  drain() stops the writer thread cleanly;
    # resume() restarts it.  Both processes then write PID-prefixed frames
    # and the reader demuxes by PID.

    def _before_fork(self):
        self._pre_fork_pid = os.getpid()
        self._fork_count = getattr(self, '_fork_count', 0)
        self.flush()
        if hasattr(self._queue, 'drain'):
            self._queue.drain()
        self._pre_fork_offset = self._fw.bytes_written

    def _after_fork_parent(self):
        self._fork_count += 1
        if hasattr(self._queue, 'resume'):
            self._queue.resume()

    def _after_fork_child(self):
        if self._fw:
            self._fw.resume()
            _write_process_info(self._fw, {
                'type': 'fork',
                'parent_pid': self._pre_fork_pid,
                'fork_index': self._fork_count,
                'parent_offset': self._pre_fork_offset,
            })
        if hasattr(self._queue, 'resume'):
            self._queue.resume()
        self._fork_count = 0


class StickyPred:
    def __init__(self, pred, extract, initial):
        self.pred = pred
        self.extract = extract
        self.value = initial

    def __call__(self, obj):
        if self.pred(obj):
            self.value = self.extract(obj)
        return self.value


def drop(pred, source):
    def f():
        obj = source()
        return f() if pred(obj) else obj
    return f


class Control:
    def __init__(self, value):
        self.value = value


class ThreadSwitch(Control):
    pass


class Heartbeat(Control):
    pass


def per_thread(source, thread, timeout):
    import retracesoftware.utils as utils

    is_control = functional.isinstanceof(Control)
    is_thread_switch = functional.isinstanceof(ThreadSwitch)

    key_fn = None

    def extract_thread_key(ts):
        if ts.value is None:
            return key_fn.value
        return ts.value

    key_fn = StickyPred(
        pred=is_thread_switch,
        extract=extract_thread_key,
        initial=thread())

    demux = utils.demux(source=source, key_function=key_fn, timeout_seconds=timeout)
    return drop(is_control, functional.sequence(thread, demux))


class reader(_backend_mod.ObjectStreamReader):

    def __init__(self, path, read_timeout, verbose, start_offset=0):
        self.stub_factory = self._default_stub_factory
        super().__init__(
            path=str(path),
            deserialize=self.deserialize,
            stub_factory=self._call_stub_factory,
            on_thread_switch=ThreadSwitch,
            create_stack_delta=lambda to_drop, frames: None,
            read_timeout=read_timeout,
            verbose=verbose,
            on_heartbeat=Heartbeat,
            start_offset=start_offset)

        self.type_deserializer = {}

    def __enter__(self): return self

    def __exit__(self, *args):
        self.close()

    def bind(self, obj):
        super().bind(obj)

    def _default_stub_factory(self, cls):
        return cls.__new__(cls)

    def _call_stub_factory(self, cls):
        return self.stub_factory(cls)
    
    def deserialize(self, bytes):
        obj = pickle.loads(bytes)
        if type(obj) in self.type_deserializer:
            return self.type_deserializer[type(obj)](obj)
        else:
            return obj


__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
