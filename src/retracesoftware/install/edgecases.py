# from .proxytype import *

import functools
import gc
import inspect
import itertools
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


def py_localpath_mkdtemp(target, system):
    """Materialize py.path tempdirs needed by passthrough file opens."""

    original = getattr(target, "__func__", target)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(cls, rootdir=None):
        path = original(cls, rootdir=rootdir)
        if getattr(system, "retrace_mode", None) == "replay":
            system.disable_for(os.makedirs)(str(path), exist_ok=True)
        return path

    return classmethod(wrapper)


def py_forkedfunc_waitfinish(target, system):
    """Record/replay ForkedFunc's child result as primitive data."""

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, *args, **kwargs):
        from py._process.forkedfunc import Result

        def result_tuple():
            result = target(self, *args, **kwargs)
            return (
                result.exitstatus,
                result.signal,
                result.retval,
                result.out,
                result.err,
            )

        return Result(*system.patch_function(result_tuple)())

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


def multiprocessing_resource_tracker_pthread_sigmask(target):
    """Run resource-tracker signal-mask bookkeeping outside the trace."""

    patched = target
    original = utils.try_unwrap(getattr(target, "_retrace_wrapped", target))

    @functools.wraps(original)
    @utils.exclude_from_stacktrace
    def wrapper(*args, **kwargs):
        frame = sys._getframe()
        while frame is not None:
            if (
                frame.f_globals.get("__name__") == "multiprocessing.resource_tracker"
                and frame.f_code.co_name == "ensure_running"
            ):
                return original(*args, **kwargs)
            frame = frame.f_back
        return patched(*args, **kwargs)

    return wrapper


def signal_signal(target, system):
    """Register retrace-aware Python signal handlers."""

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(signalnum, handler):
        if (
            callable(handler)
            and not utils.is_wrapped(handler)
            and not getattr(handler, "__retrace_signal_trampoline__", False)
        ):
            wrapped_handler = system.wrap_async(handler)
            signal_handler_targets = getattr(system, "_signal_handler_targets", None)
            if signal_handler_targets is None:
                signal_handler_targets = {}
                system._signal_handler_targets = signal_handler_targets
            signal_handler_targets[wrapped_handler] = handler

            @functools.wraps(handler)
            @utils.exclude_from_stacktrace
            def retrace_signal_trampoline(signum, frame):
                if system.location is None:
                    return handler(signum, frame)
                return system.gate.apply_with("external", wrapped_handler)(
                    signum,
                    frame,
                )

            retrace_signal_trampoline.__retrace_signal_trampoline__ = True
            system.bind(retrace_signal_trampoline)
            handler = retrace_signal_trampoline
        return target(signalnum, handler)

    return wrapper


def _pytest_cache_method_with_payload_token(target, system):
    target = utils.try_unwrap(target)
    disabled_target = system.disable_for(target, unwrap_args=False)
    counter = itertools.count()
    payloads = {}
    wrapped_external = None

    @utils.exclude_from_stacktrace
    def external(key, token):
        self, payload = payloads.pop(token)
        return disabled_target(self, key, payload)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, key, payload):
        nonlocal wrapped_external
        if wrapped_external is None:
            wrapped_external = system.patch_function(external)
        token = next(counter)
        payloads[token] = (self, payload)
        return wrapped_external(key, token)

    return wrapper


def pytest_cache_get(target, system):
    """Record/replay Cache.get without exposing mutable defaults as call args."""

    return _pytest_cache_method_with_payload_token(target, system)


def pytest_cache_for_config(target, system):
    """Keep pytest --cache-clear branch decisions tied to recorded cache state."""

    target = utils.try_unwrap(target)
    cls = getattr(target, "__self__", None)
    if not isinstance(cls, type):
        return target

    counter = itertools.count()
    cachedirs = {}
    wrapped_cachedir_exists = None

    @utils.exclude_from_stacktrace
    def cachedir_exists(token):
        return cachedirs.pop(token).is_dir()

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(config, *, _ispytest=False):
        nonlocal wrapped_cachedir_exists
        if not config.getoption("cacheclear"):
            return target(config, _ispytest=_ispytest)

        cachedir = cls.cache_dir_from_config(config, _ispytest=_ispytest)
        token = next(counter)
        cachedirs[token] = cachedir
        if wrapped_cachedir_exists is None:
            wrapped_cachedir_exists = system.patch_function(cachedir_exists)
        if wrapped_cachedir_exists(token):
            cls.clear_cache(cachedir, _ispytest=_ispytest)
        return cls(cachedir, config, _ispytest=_ispytest)

    return wrapper


def _is_pytest_control_env_key(key):
    return key in {"PYTEST_VERSION", "PYTEST_CURRENT_TEST"} or key in {
        b"PYTEST_VERSION",
        b"PYTEST_CURRENT_TEST",
    }


def pytest_get_config_invocation_dir(target, system):
    """Keep pytest invocation-directory bookkeeping out of the app trace."""

    target = utils.try_unwrap(target)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(*args, **kwargs):
        original_getcwd = os.getcwd
        disabled_getcwd = system.disable_for(original_getcwd, unwrap_args=False)
        os.getcwd = disabled_getcwd
        try:
            return target(*args, **kwargs)
        finally:
            os.getcwd = original_getcwd

    return wrapper


def pytest_main_control_env(target, system):
    """Keep pytest's session marker out of the application trace.

    Pytest sets and later restores markers such as ``PYTEST_VERSION`` and
    ``PYTEST_CURRENT_TEST`` around framework execution. Those markers are
    pytest control-plane state; tracing them can desync when coverage.py,
    pytest-cov, or pytest cleanup shift startup/shutdown calls around the
    measured execution. Other environment mutations still pass through the
    normal Retrace wrappers.
    """

    target = utils.try_unwrap(target)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(*args, **kwargs):
        patched_putenv = os.putenv
        patched_unsetenv = os.unsetenv
        disabled_putenv = system.disable_for(patched_putenv, unwrap_args=False)
        disabled_unsetenv = system.disable_for(patched_unsetenv, unwrap_args=False)

        environ_type = type(os.environ)
        setitem_globals = environ_type.__setitem__.__globals__
        delitem_globals = environ_type.__delitem__.__globals__
        original_setitem_putenv = setitem_globals.get("putenv")
        original_delitem_unsetenv = delitem_globals.get("unsetenv")

        @functools.wraps(patched_putenv)
        @utils.exclude_from_stacktrace
        def putenv(key, value):
            if _is_pytest_control_env_key(key):
                return disabled_putenv(key, value)
            return patched_putenv(key, value)

        @functools.wraps(patched_unsetenv)
        @utils.exclude_from_stacktrace
        def unsetenv(key):
            if _is_pytest_control_env_key(key):
                return disabled_unsetenv(key)
            return patched_unsetenv(key)

        os.putenv = putenv
        os.unsetenv = unsetenv
        setitem_globals["putenv"] = putenv
        delitem_globals["unsetenv"] = unsetenv
        try:
            return target(*args, **kwargs)
        finally:
            os.putenv = patched_putenv
            os.unsetenv = patched_unsetenv
            if original_setitem_putenv is None:
                setitem_globals.pop("putenv", None)
            else:
                setitem_globals["putenv"] = original_setitem_putenv
            if original_delitem_unsetenv is None:
                delitem_globals.pop("unsetenv", None)
            else:
                delitem_globals["unsetenv"] = original_delitem_unsetenv

    return wrapper


def pytest_register_cleanup_lock_removal(target, system):
    """Keep pytest tempdir cleanup-lock callbacks out of the app trace."""

    target = utils.try_unwrap(target)
    disabled_target = system.disable_for(target, unwrap_args=False)
    try:
        register_param = inspect.signature(target).parameters.get("register")
    except (TypeError, ValueError):
        register_param = None
    register_kind = None if register_param is None else register_param.kind
    if register_param is None or register_param.default is inspect.Parameter.empty:
        default_register = _DEFAULT
    else:
        default_register = register_param.default

    def disabled_register_for(register):
        @utils.exclude_from_stacktrace
        def disabled_register(callback, *args, **kwargs):
            callback = system.disable_for(callback, unwrap_args=False)
            return register(callback, *args, **kwargs)

        return disabled_register

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(lock_path, register=_DEFAULT, *args, **kwargs):
        if "register" in kwargs:
            kwargs = dict(kwargs)
            kwargs["register"] = disabled_register_for(kwargs["register"])
            return disabled_target(lock_path, *args, **kwargs)
        if register is _DEFAULT:
            if default_register is _DEFAULT:
                return disabled_target(lock_path, *args, **kwargs)
            register = default_register
        if register_kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs = dict(kwargs)
            kwargs["register"] = disabled_register_for(register)
            return disabled_target(lock_path, *args, **kwargs)
        return disabled_target(lock_path, disabled_register_for(register), *args, **kwargs)

    return wrapper


def _disable_coverage_tracer_callbacks(tracer, system):
    callback_attrs = (
        "check_include",
        "concur_id_func",
        "disable_plugin",
        "lock_data",
        "should_start_context",
        "should_trace",
        "switch_context",
        "unlock_data",
        "warn",
    )

    for attr in callback_attrs:
        if not hasattr(tracer, attr):
            continue
        callback = getattr(tracer, attr)
        if callback is None or not callable(callback):
            continue
        if getattr(callback, "__retrace_disabled_thread_target__", False):
            continue
        setattr(tracer, attr, system.disable_for(callback, unwrap_args=False))


def coverage_collector_start_tracer(target, system):
    """Keep coverage.py tracer callbacks out of Retrace's app boundary.

    coverage.py installs a C or Python trace function to observe the user code
    being measured. That trace function calls Python callbacks such as
    ``should_trace`` and ``switch_context`` from inside coverage's own
    instrumentation path. Those callbacks are coverage control-plane work, not
    application behavior, so they must not consume record/replay messages.
    """

    target = utils.try_unwrap(target)
    disabled_target = system.disable_for(target, unwrap_args=False)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, *args, **kwargs):
        result = disabled_target(self, *args, **kwargs)
        tracers = getattr(self, "tracers", ())
        if tracers:
            _disable_coverage_tracer_callbacks(tracers[-1], system)
        return result

    return wrapper


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


def _pytest_xdist_active(config):
    if hasattr(config, "workerinput") or hasattr(config, "slaveinput"):
        return True
    option = getattr(config, "option", None)
    numprocesses = getattr(option, "numprocesses", None)
    return numprocesses not in (None, 0, "0")


def pytest_rerunfailures_configure(target, system):
    """Avoid pytest-rerunfailures' xdist status server in serial pytest runs."""

    disabled = system.disable_for(utils.try_unwrap(target), unwrap_args=False)

    @functools.wraps(target)
    def wrapper(config, *args, **kwargs):
        pluginmanager = getattr(config, "pluginmanager", None)
        hasplugin = getattr(pluginmanager, "hasplugin", None)
        if (
            hasplugin is not None
            and hasplugin("xdist")
            and not _pytest_xdist_active(config)
        ):
            def serial_hasplugin(name):
                if name == "xdist":
                    return False
                return hasplugin(name)

            pluginmanager.hasplugin = serial_hasplugin
            try:
                return disabled(config, *args, **kwargs)
            finally:
                pluginmanager.hasplugin = hasplugin

        return disabled(config, *args, **kwargs)

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
