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
import re
from types import ModuleType
from typing import Any

import retracesoftware.functional as functional


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

def extract_frozen_name(filename):
    match = re.match(r"<frozen (.+)>", filename)
    return match.group(1) if match else None


def normalize_path(path):
    """
    Normalize a path to an absolute path for recording.
    
    Recording stores absolute paths. Replay uses path_info from settings.json
    to map paths appropriately.
    """
    # Handle frozen modules - get their actual file path
    frozen_name = extract_frozen_name(path)
    if frozen_name:
        mod = sys.modules.get(frozen_name)
        if mod and hasattr(mod, '__file__') and mod.__file__:
            return os.path.realpath(mod.__file__)
        return path  # fallback to original if module not found
    
    # Return canonical absolute path
    try:
        return os.path.realpath(path)
    except (OSError, TypeError):
        return path


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

    Returns (info_dict, byte_offset) where byte_offset is the file
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

    def __init__(self, path=None, thread=None, output=None,
                 flush_interval=0.1,
                 verbose=False,
                 disable_retrace=None,
                 preamble=None,
                 inflight_limit=None,
                 stall_timeout=None,
                 queue_capacity=None,
                 return_queue_capacity=None,
                 quit_on_error=False,
                 serialize_errors=True,
                 raw=False):

        self._fw = None

        if output is None and path is not None:
            fw = _backend_mod.FramedWriter(str(path), raw=raw)
            self._fw = fw

            if preamble is not None:
                _write_process_info(fw, preamble)

            output = _backend_mod.AsyncFilePersister(fw)

        self._output = output
        self._disable_retrace = disable_retrace

        kwargs = dict(thread=thread, serializer=self.serialize,
                      verbose=verbose,
                      normalize_path=normalize_path)
        if inflight_limit is not None:
            kwargs['inflight_limit'] = inflight_limit
        if stall_timeout is not None:
            kwargs['stall_timeout'] = stall_timeout
        if queue_capacity is not None:
            kwargs['queue_capacity'] = queue_capacity
        if return_queue_capacity is not None:
            kwargs['return_queue_capacity'] = return_queue_capacity
        if quit_on_error:
            kwargs['quit_on_error'] = quit_on_error
        if not serialize_errors:
            kwargs['serialize_errors'] = False

        super().__init__(output, **kwargs)

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
        self.flush()
        self.disable()
        if hasattr(self, '_output') and self._output and hasattr(self._output, 'close'):
            self._output.close()
        if hasattr(self, '_fw') and self._fw and hasattr(self._fw, 'close'):
            self._fw.close()
        self._output = None
        self._fw = None

    def serialize(self, obj):
        return self.type_serializer.get(type(obj), pickle.dumps)(obj)

    def heartbeat(self):
        import resource
        payload = {
            'ts': time.time(),
            'inflight': self.inflight_bytes,
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
        if hasattr(self._output, 'drain'):
            self._output.drain()
        self._pre_fork_offset = self._fw.bytes_written

    def _after_fork_parent(self):
        self._fork_count += 1
        if hasattr(self._output, 'resume'):
            self._output.resume()

    def _after_fork_child(self):
        if self._fw:
            self._fw.resume()
            _write_process_info(self._fw, {
                'type': 'fork',
                'parent_pid': self._pre_fork_pid,
                'fork_index': self._fork_count,
                'parent_offset': self._pre_fork_offset,
            })
        if hasattr(self._output, 'resume'):
            self._output.resume()
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


class Bind(Control):
    pass


class ThreadSwitch(Control):
    pass


class Heartbeat(Control):
    pass


def _resolve_binds(source):
    """Wrap *source* so that Bind markers are resolved immediately.

    The C++ reader sets ``pending_bind`` when it emits a Bind sentinel.
    ``bind(None)`` must be called before the next read — otherwise any
    read-ahead (e.g. by demux) will hit the pending-bind error.
    """
    def f():
        obj = source()
        while isinstance(obj, Bind):
            obj.value(None)
            obj = source()
        return obj
    return f


def per_thread(source, thread, timeout):
    import retracesoftware.utils as utils

    is_control = functional.isinstanceof(Control)
    is_thread_switch = functional.isinstanceof(ThreadSwitch)

    key_fn = StickyPred(
        pred=is_thread_switch,
        extract=lambda ts: ts.value,
        initial=thread())

    demux = utils.demux(source=_resolve_binds(source), key_function=key_fn, timeout_seconds=timeout)
    return drop(is_control, functional.sequence(thread, demux))


class reader(_backend_mod.ObjectStreamReader):

    def __init__(self, path, read_timeout, verbose, start_offset=0, raw=False):
        super().__init__(
            path=str(path),
            deserialize=self.deserialize,
            bind_singleton=Bind(self.bind),
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
    
    def deserialize(self, bytes):
        obj = pickle.loads(bytes)
        if type(obj) in self.type_deserializer:
            return self.type_deserializer[type(obj)](obj)
        else:
            return obj


__all__ = sorted([k for k in globals().keys() if not k.startswith("_")])
