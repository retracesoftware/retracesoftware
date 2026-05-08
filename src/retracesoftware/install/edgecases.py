# from .proxytype import *

import functools
import gc
import os
import sys

from retracesoftware.install import globals
import retracesoftware.utils as utils

_REAL_GETPID = os.getpid

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


def _live_getpid():
    value = _REAL_GETPID
    while True:
        wrapped = getattr(value, "__wrapped__", value)
        if wrapped is not value:
            value = wrapped
            continue

        unwrapped = utils.try_unwrap(value)
        if unwrapped is value:
            return value()
        value = unwrapped


def multiprocessing_finalize_call(target):
    """Use the live PID guard for multiprocessing weakref finalizers."""

    defaults = target.__defaults__ or ()
    wr_default = defaults[0] if len(defaults) > 0 else None
    registry_default = defaults[1] if len(defaults) > 1 else _DEFAULT
    debug_default = defaults[2] if len(defaults) > 2 else _DEFAULT

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(
        self,
        wr=wr_default,
        _finalizer_registry=registry_default,
        sub_debug=debug_default,
        getpid=_DEFAULT,
    ):
        if getpid is _DEFAULT:
            getpid = _live_getpid
        return target(self, wr, _finalizer_registry, sub_debug, getpid)

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


typewrappers = {
    '_socket': {
        'socket': {
            'recvfrom_into': recvfrom_into,
            'recv_into': recv_into,
            'recvmsg_into': recvmsg_into
        }
    },
    'socket': {
        'SocketIO': {
            'readinto': readinto
        }
    },
    '_ssl': {
        '_SSLSocket': {
            'read': read,
            # 'write': write
        }
    },
    'io': {
        'FileIO': {
            'readinto': readinto
        },
        'BufferedReader': {
            'readinto': readinto,
            'readinto1': readinto1
        },
        'BufferedRandom': {
            'readinto': readinto,
            'readinto1': readinto1
        },
        'BufferedRWPair': {
            'readinto': readinto,
            'readinto1': readinto1
        }
    },
    'mmap': {
        'mmap': {
            'readinto': mmap_readinto
        }
    }
}

def patchtype(module, name, cls : type):
    if module in typewrappers:
        if name in typewrappers[module]:
            for method,patcher in typewrappers[module][name].items():
                setattr(cls, method, patcher(getattr(cls, method)))

def typepatcher(cls : type):

    # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! type: {cls} created')
    # traceback.print_stack()
    
    return typewrappers.get(cls.__module__, {}).get(cls.__name__, {})

    # if cls.__module__ in typewrappers:
    #     # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! TYPEWRAPPER for {cls}')

    #     mod = typewrappers[cls.__module__]

    #     return mod.get(cls.__name__, {})
    
    #     if cls.__name__ in mod:
    #         # if cls.__name__ == '_SSLSocket':
    #         #     breakpoint()

    #         log.info("Applying specialized typewrapper to %s, updated slots: %s", cls, list(mod[cls.__name__].keys()))

    #         for name,value in mod[cls.__name__].items():
    #             setattr(cls, name, value(getattr(cls, name)))

    #         # slots = {'__module__': cls.__module__, '__slots__': ()}
    #         # slots.update(mod[cls.__name__])
    #         # return type(cls.__name__, (cls, ), slots)

    # return cls

def typewrapper(cls : type):

    # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! type: {cls} created')
    # traceback.print_stack()
    
    if cls.__module__ in typewrappers:
        # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! TYPEWRAPPER for {cls}')

        mod = typewrappers[cls.__module__]

        if cls.__name__ in mod:
            # if cls.__name__ == '_SSLSocket':
            #     breakpoint()

            log.info("Applying specialized typewrapper to %s, updated slots: %s", classname(cls), list(mod[cls.__name__].keys()))

            slots = {'__module__': cls.__module__, '__slots__': ()}
            slots.update(mod[cls.__name__])
            return type(cls.__name__, (cls, ), slots)

    return cls
