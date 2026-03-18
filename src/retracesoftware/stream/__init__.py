"""
retracesoftware.stream - Runtime selectable release/debug builds

Set RETRACE_DEBUG=1 to use the debug build with symbols and assertions.
"""
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

    def _consume_index(self, name, value):
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

    def _remember_binding(self, ref_id, index):
        self._bindings[ref_id] = index
        self._next_binding_index = max(self._next_binding_index, index + 1)

    def reset_state(self):
        self._handle_indices = {}
        self._next_handle_index = 0
        self._bindings = {}
        self._next_binding_index = 0

    def consume_object(self, obj):
        return self._dispatch_event(_debug_object_event(obj))

    def consume_handle_ref(self, ref_id):
        return self._consume_index("handle_ref", self._handle_index(ref_id))

    def consume_handle_delete(self, ref_id):
        delta = self._handle_delete_delta(ref_id)
        if delta is None:
            return None
        return self._consume_index("handle_delete", delta)

    def consume_ref(self, ref_id):
        index = self._bindings[ref_id]
        return self._consume_index("bound_ref", index)

    def consume_intern(self, obj):
        ref_id = id(obj)
        index = self._bind_index(ref_id)
        self._remember_binding(ref_id, index)
        return self._dispatch_event(
            _debug_command_event("intern", (index, _debug_object_event(obj)))
        )

    def consume_bound_ref_delete(self, ref_id):
        index = self._bindings.pop(ref_id, None)
        if index is None:
            return None
        return self._consume_index("bound_ref_delete", index)

    def consume_flush(self):
        return self._command0("flush")

    def consume_shutdown(self):
        return self._command0("shutdown")

    def consume_list(self, length):
        return self._command1("list", length)

    def consume_tuple(self, length):
        return self._command1("tuple", length)

    def consume_dict(self, length):
        return self._command1("dict", length)

    def consume_heartbeat(self):
        return self._command0("heartbeat")

    def consume_new_ext_wrapped(self, typ):
        return self._dispatch_event(
            _debug_command_event("new_ext_wrapped", (_debug_object_event(typ),))
        )

    def consume_delete(self, ref_id):
        return self._command1("delete", ref_id)

    def consume_thread_switch(self, thread_handle):
        return self._command1("thread_switch", thread_handle)

    def consume_pickled(self, obj):
        return self._dispatch_event(
            _debug_command_event("pickled", (_debug_object_event(obj),))
        )

    def consume_new_handle(self, ref_id, obj):
        index = self._handle_index(ref_id)
        return self._dispatch_event(
            _debug_command_event("new_handle", (index, _debug_object_event(obj)))
        )

    def consume_new_patched(self, obj, typ):
        ref_id = id(obj)
        self._remember_binding(ref_id, self._bind_index(ref_id))
        return self._dispatch_event(
            _debug_command_event(
                "new_patched",
                (_debug_object_event(type(obj)), _debug_object_event(typ)),
            )
        )

    def consume_bind(self, ref_id):
        index = self._bind_index(ref_id)
        self._remember_binding(ref_id, index)
        return self._command1("bind", index)

    def consume_serialize_error(self):
        return self._command0("serialize_error")

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
    at the start of binary data (first PID frame or raw message)."""
    peek = f.read(2)
    if peek == b'#!':
        f.readline()
    elif peek:
        f.seek(-len(peek), 1)


def detect_raw_trace(path):
    """Return True for raw traces, False for PID-framed traces."""
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
    headers are parsed to reassemble the JSON payload.  When *raw* is
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

    def __init__(self, path=None, thread=None, output=None, queue=None,
                 flush_interval=0.1,
                 verbose=False,
                 disable_retrace=None,
                 preamble=None,
                 push_fail_callback=None,
                 inflight_limit=None,
                 consumer_wait_timeout_ms=None,
                 queue_capacity=None,
                 quit_on_error=False,
                 serialize_errors=True,
                 validate_bindings=False,
                 raw=False):

        self._fw = None
        self._queue = queue

        queue_kwargs = {}
        if inflight_limit is not None:
            queue_kwargs["inflight_limit"] = inflight_limit
        if consumer_wait_timeout_ms is not None:
            queue_kwargs["consumer_wait_timeout_ms"] = consumer_wait_timeout_ms
        if queue_capacity is not None:
            queue_kwargs["queue_capacity"] = queue_capacity
        if thread is not None:
            queue_kwargs["thread"] = thread
        if push_fail_callback is not None:
            queue_kwargs["push_fail_callback"] = push_fail_callback

        if self._queue is None and path is not None:
            fw = _backend_mod.FramedWriter(str(path), raw=raw)
            self._fw = fw

            if preamble is not None:
                _write_process_info(fw, preamble)

            output = _backend_mod.Persister(
                fw,
                serializer=self.serialize,
            )

        if self._queue is None and output is not None:
            self._queue = _backend_mod.Queue(output, **queue_kwargs)

        if self._queue is None:
            raise ValueError("writer requires a queue, output, or path")
        if inflight_limit is not None and self._queue is not None:
            self._queue.inflight_limit = inflight_limit

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

        if path is not None and hasattr(os, 'register_at_fork'):
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
            import resource
            payload = {
                'ts': time.time(),
                'inflight': self.queue.inflight_bytes,
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

    key_fn = StickyPred(
        pred=is_thread_switch,
        extract=lambda ts: ts.value,
        initial=thread())

    demux = utils.demux(source=source, key_function=key_fn, timeout_seconds=timeout)
    return drop(is_control, functional.sequence(thread, demux))


class reader(_backend_mod.ObjectStreamReader):

    def __init__(self, path, read_timeout, verbose, start_offset=0, raw=False):
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
