import functools
import gc
import inspect
import os
import sys

import retracesoftware.functional as functional
from retracesoftware.install import globals
import retracesoftware.utils as utils

def recvfrom_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data, address = self.recvfrom(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data), address
    return wrapper
def recv_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data = self.recv(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data)
    return wrapper

def recvmsg_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffers, ancbufsize = 0, flags = 0):
        raise NotImplementedError('TODO')
    return wrapper

def read(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, *args):
        # super_type = super(type(self), self)

        if len(args) == 0:
            return target(self)
        else:
            buflen = args[0]

            # pdb.set_trace()

            data = target(self, buflen)

            if len(args) == 1:
                return data
            else:
                buffer = args[1]

                buffer[0:len(data)] = data

                return len(data)
    return wrapper

def write(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, byteslike):
        return target(byteslike.tobytes())

    return wrapper

def readinto(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        bytes = self.read(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper

def readinto1(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        bytes = self.read1(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper

def mmap_readinto(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)
    return wrapper

def openssl_set_verify(target):
    target = utils.try_unwrap(target)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, mode, callback=None):
        if not hasattr(self, "_used"):
            return None
        if callback is not None:
            callback = utils.try_unwrap(callback)
        return target(self, mode, callback)
    return wrapper


def collect_before(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(*args, **kwargs):
        gc.collect()
        return target(*args, **kwargs)

    return wrapper


_DEFAULT = object()


def subprocess_internal_poll(target):
    """Route Popen's cached waitpid default through Retrace's patched os module."""

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(
        self,
        _deadstate=None,
        _waitpid=_DEFAULT,
        _WNOHANG=_DEFAULT,
        _ECHILD=_DEFAULT,
    ):
        import errno

        if _waitpid is _DEFAULT:
            _waitpid = os.waitpid
        if _WNOHANG is _DEFAULT:
            _WNOHANG = os.WNOHANG
        if _ECHILD is _DEFAULT:
            _ECHILD = errno.ECHILD
        return target(self, _deadstate, _waitpid, _WNOHANG, _ECHILD)

    return wrapper


def pthread_sigmask(target):
    raw_target = utils.try_unwrap(target)

    @functools.wraps(raw_target)
    @utils.exclude_from_stacktrace
    def wrapper(how, mask):
        mask = utils.try_unwrap(mask)
        if isinstance(mask, set):
            mask = tuple(mask)
        result = target(how, mask)
        result_unwrapped = utils.try_unwrap(result)
        if isinstance(result_unwrapped, set):
            return tuple(result_unwrapped)
        return result

    return wrapper


def _sync_truthy_result(result_reader, sync):
    def synced(*args, **kwargs):
        result = result_reader(*args, **kwargs)
        if result:
            sync(*args, **kwargs)
        return result

    return synced


def _record_result(target, writer):
    return functional.sequence(
        target,
        functional.side_effect(writer.result),
    )


def _replay_result_with_truthy_sync(target, reader):
    return _sync_truthy_result(reader.result, target)


def _signature_or_none(target):
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


def _acquire_args(signature, self, args, kwargs):
    if signature is not None:
        try:
            bound = signature.bind(self, *args, **kwargs)
        except TypeError:
            return None
        return (
            bound.arguments.get("blocking", True),
            bound.arguments.get("timeout", None),
        )

    if len(args) > 2:
        return None
    if any(name not in {"blocking", "timeout"} for name in kwargs):
        return None
    if args and "blocking" in kwargs:
        return None
    if len(args) > 1 and "timeout" in kwargs:
        return None

    blocking = args[0] if args else kwargs.get("blocking", True)
    timeout = args[1] if len(args) > 1 else kwargs.get("timeout", None)
    return blocking, timeout


def _in_internal_space(system):
    return getattr(system, "location", "internal") == "internal"


def acquire_trylock(target, *, system=None):
    raw_target = utils.try_unwrap(target)
    signature = _signature_or_none(raw_target)
    trylock = None
    if system is not None:
        record_replay_operation = getattr(system, "record_replay_operation", None)
        if record_replay_operation is not None:
            trylock = record_replay_operation(
                functional.partial(_record_result, raw_target),
                functional.partial(_replay_result_with_truthy_sync, raw_target),
            )

    @functools.wraps(raw_target)
    @utils.exclude_from_stacktrace
    def wrapper(self, *args, **kwargs):
        acquire_args = _acquire_args(signature, self, args, kwargs)
        if acquire_args is None:
            return raw_target(self, *args, **kwargs)

        blocking, timeout = acquire_args
        if blocking is False and timeout is not None:
            return raw_target(self, *args, **kwargs)

        if blocking is False or timeout == 0:
            operation = (
                trylock
                if trylock is not None and _in_internal_space(system)
                else raw_target
            )
            return operation(self, *args, **kwargs)

        return raw_target(self, *args, **kwargs)

    return wrapper


def semaphore_acquire_trylock(target, *, system=None):
    return acquire_trylock(target, system=system)


def asyncio_write_to_self(target, *, system=None):
    raw_target = utils.try_unwrap(target)

    @functools.wraps(raw_target)
    @utils.exclude_from_stacktrace
    def wrapper(self, *args, **kwargs):
        result = target(self, *args, **kwargs)

        replaying = getattr(system, "retrace_mode", None) == "replay"
        if not replaying:
            return result

        handoff_schedule_to = getattr(
            system,
            "handoff_replay_thread_schedule_to",
            None,
        )
        consume_live = (
            system.disable_for(raw_target, unwrap_args=False)
            if system is not None
            else raw_target
        )

        loop_thread_id = getattr(self, "_thread_id", None)
        if handoff_schedule_to is not None and loop_thread_id is not None:
            handoff_schedule_to(loop_thread_id)
        consume_live(self, *args, **kwargs)
        return result

    return wrapper


def concurrent_futures_threadpool_shutdown_sentinel(*args, **kwargs):
    if len(args) < 2 or args[1] is not None:
        return False

    frame = sys._getframe()
    while frame is not None:
        if (
            frame.f_globals.get("__name__") == "concurrent.futures.thread"
            and frame.f_code.co_name in {"_worker", "shutdown"}
        ):
            return True
        frame = frame.f_back
    return False


def openssl_connection_class(cls):
    class Connection(cls):
        __module__ = cls.__module__
        __qualname__ = cls.__qualname__

        def close(self):
            try:
                sock = object.__getattribute__(self, "_socket")
            except AttributeError:
                return None
            if sock is None:
                return None

            import _socket

            if isinstance(sock, _socket.socket):
                try:
                    sock._closed = True
                except Exception:
                    pass
                try:
                    io_refs = sock._io_refs
                except Exception:
                    io_refs = 0
                if io_refs <= 0:
                    return utils.try_unwrap_apply(_socket.socket.close, sock)
                return None

            return utils.try_unwrap_apply(getattr(sock, "close"))

    Connection.__name__ = cls.__name__
    return Connection


def fsspec_cached_call(target):
    """Keep fsspec's PID cache check to one recorded boundary call.

    fsspec initializes class-level cache PIDs while imports are disabled, then
    compares that value with os.getpid() during filesystem construction. Replay
    sees a live import-time PID but a recorded os.getpid() result, so the stock
    implementation calls os.getpid() a second time to refresh the cache.
    """
    target_globals = target.__globals__

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(cls, *args, **kwargs):
        apply_config = target_globals["apply_config"]
        tokenize = target_globals["tokenize"]
        threading = target_globals["threading"]
        os = target_globals["os"]

        kwargs = apply_config(cls, kwargs)
        extra_tokens = tuple(
            getattr(cls, attr, None)
            for attr in getattr(cls, "_extra_tokenize_attributes", ())
        )
        strip_tokenize_options = {
            k: kwargs.pop(k)
            for k in getattr(cls, "_strip_tokenize_options", ())
            if k in kwargs
        }
        current_pid = os.getpid()
        token = tokenize(
            cls,
            current_pid,
            threading.get_ident(),
            *args,
            *extra_tokens,
            **kwargs,
        )
        skip = kwargs.pop("skip_instance_cache", False)
        if current_pid != cls._pid:
            cls._cache.clear()
            cls._pid = current_pid
        if not skip and cls.cachable and token in cls._cache:
            cls._latest = token
            return cls._cache[token]

        obj = type.__call__(cls, *args, **kwargs, **strip_tokenize_options)
        obj._fs_token_ = token
        obj.storage_args = args
        obj.storage_options = kwargs
        if obj.async_impl and obj.mirror_sync_methods:
            from fsspec.asyn import mirror_sync_methods

            mirror_sync_methods(obj)

        if cls.cachable and not skip:
            cls._latest = token
            cls._cache[token] = obj
        return obj

    return wrapper
