"""Gate-based proxy runtime state.

`System` owns the long-lived boundary state used by the proxy layer:

- the phase gate that distinguishes internal from external execution
- proxy/unproxy helpers and passthrough predicates
- patch/unpatch support for types and callables
- thread inheritance and install-time wiring

Mode-specific behavior such as recording, replay reads, checkpoints,
and callback/result hooks is assembled elsewhere and injected through
gateway factories plus lifecycle/hook attributes.
"""

from typing import NamedTuple, Callable, Any
import retracesoftware.functional as functional
import retracesoftware.stream as stream
import retracesoftware.utils as utils
import types
import gc
import threading

from retracesoftware.proxy.proxytype import method_names, superdict
from retracesoftware.proxy.proxytype import DynamicProxy
from retracesoftware.install.patcher import install_hash_patching
from retracesoftware.install.edgecases import patchtype
from retracesoftware.proxy.gateway import ext_gateway, int_gateway
from retracesoftware.proxy.patchtype import patch_type, _module_unpatch_type

def proxy(proxytype_from):
    """Create a callable that wraps a value in a proxy type."""
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, proxytype_from),
        functional.identity,
    )

class ProxyRef:
    def __init__(self, cls):
        self.cls = cls

    def __call__(self):
        return utils.create_wrapped(self.cls, None)

def lookup(module, name):
    import sys

    if module in sys.modules:
        if name in sys.modules[module].__dict__:
            return sys.modules[module].__dict__[name]
    return None

class LifecycleHooks(NamedTuple):
    on_start: Callable[..., Any] | None = None
    on_end: Callable[..., Any] | None = None

class CallHooks(NamedTuple):
    """Lifecycle callbacks for one side of the proxy boundary."""

    on_call: Callable[..., Any] | None = None
    on_result: Callable[..., Any] | None = None
    on_error: Callable[..., Any] | None = None

class ThreadSafeCounter:
    def __init__(self, initial=0):
        self._value = initial
        self._lock = threading.Lock()

    def next(self):
        with self._lock:
            value = self._value
            self._value += 1
            return value

    def peek(self):
        with self._lock:
            return self._value

def when_instanceof(cls, on_then, on_else = functional.identity):
    return functional.if_then_else(functional.isinstanceof(cls), on_then, on_else)

def with_type_of(func):
    return functional.sequence(functional.typeof, func)

def _ext_proxytype_from_spec(system, module, name, methods):
    spec = {
        '__module__': module,
    }

    cls = lookup(module, name)

    def unbound_function(name):
        return lambda instance, *args, **kwargs: getattr(instance, name)(*args, **kwargs)

    def proxy(name):
        if cls is not None and isinstance(cls, type) and hasattr(cls, name):
            return getattr(cls, name)

        return unbound_function(name)

    for method in methods:
        spec[method] = system._wrapped_function(handler=system.ext_gateway, target=proxy(method))

    proxytype = type(name, (utils.ExternalWrapped, DynamicProxy,), spec)

    patchtype(module=module, name=name, cls=proxytype)
    system.bind(proxytype)
    system.bind(system.proxy_ref(proxytype))

    return proxytype

fallback = functional.mapargs(transform = functional.walker(utils.try_unwrap), function = functional.apply)


class System:
    """Mutable runtime state shared by record and replay assemblers."""

    def wrap_start_new_thread(self, original_start_new_thread):
        """Wrap ``start_new_thread`` so child threads inherit active retrace state."""
        def wrap_thread_function(function):
            if self.enabled():
                next_id = self.counter.next()
                wrapped = self.thread_wrapper(function)

                def in_child(*args, **kwargs):
                    self._thread_id.set(next_id)
                    return wrapped(*args, **kwargs)

                return in_child
            else:
                return function

        def wrapped_start_new_thread(function, args, kwargs=None):
            wrapped = wrap_thread_function(function)
            if kwargs is None:
                return original_start_new_thread(wrapped, args)
            return original_start_new_thread(wrapped, args, kwargs)

        return wrapped_start_new_thread

    def patch_function(self, fn):
        """Return a wrapper that routes *fn* through the external gate.

        Use this for standalone module-level functions (e.g.
        ``time.time``, ``os.getpid``) that need to be recorded and
        replayed.
        """
        self.bind(fn)
        return self._wrapped_function(self.ext_gateway, fn)

    def patch(self, obj, install_session=None):
        """Patch *obj* for proxying — dispatches by type.

        If *obj* is a class, delegates to ``patch_type`` (mutates the
        class in-place, returns ``None``).

        If *obj* is a callable (function, builtin, etc.), delegates to
        ``patch_function`` (returns a new ``BoundGate`` wrapper).

        Raises ``TypeError`` for anything else.
        """
        if isinstance(obj, type):
            self.patch_type(obj, install_session=install_session)
            return obj
        if callable(obj):
            return self.patch_function(obj)
        raise TypeError(f"cannot patch {type(obj).__name__!r} object")

    def disable_for(self, function, *, unwrap_args=True):
        """Return a callable that runs *function* with the phase gate cleared.

        This is used when the system needs to call its own internal
        helpers (e.g. proxytype factories) without triggering the
        adapter pipeline.

        ``function`` may itself be a ``wrapped_function``. In that case we
        still want to execute its underlying target rather than re-entering
        the wrapper/handler path with the gate cleared.

        When ``unwrap_args`` is true, the disabled call also recursively
        unwraps nested args/kwargs through ``fallback``. When false, it
        only unwraps the callable itself and passes args/kwargs through
        unchanged via ``utils.try_unwrap_apply``.
        """
        disabled = fallback if unwrap_args else utils.try_unwrap_apply
        return self.gate.apply_with(None, functional.partial(disabled, function))

    def __init__(self, on_bind = None) -> None:

        self.gate = utils.ThreadLocal(None)
        self.int_gateway = self.gate.if_then_else('external', fallback, fallback)
        self.ext_gateway = self.gate.if_then_else('internal', fallback, fallback)

        self.ext_gateway_factory = ext_gateway
        self.int_gateway_factory = int_gateway

        self.lifecycle_hooks = LifecycleHooks(
            on_start = None,
            on_end = None,
        )

        self.patched_types = set()
        self.immutable_types = set()        
        self.is_bound = utils.WeakSet()

        self.enabled = lambda: self.gate.get() is not None

        self.bind = utils.runall(self.is_bound.add, on_bind)

        self.async_new_patched = utils.noop

        self.create_stub_object = utils.runall(self.is_bound.add, utils.create_stub_object)
        self.is_retraced = functional.or_predicate(self.is_bound, utils.is_wrapped)
        self.is_patched = lambda obj: (
            (isinstance(obj, type) and obj in self.patched_types)
            or type(obj) in self.patched_types
        )

        self.is_patched_type = utils.FastTypePredicate(lambda cls: cls in self.patched_types).istypeof
        self._on_alloc = self.gate.cond(
            'internal', self._call_bind,
            'external', self._call_async_new_patched,
            utils.noop)

        self.ext_proxytype_from_spec = self._wrapped_function(self.int_gateway, _ext_proxytype_from_spec)
        self.bind(self)

        self.proxy_ref = functional.memoize_one_arg(ProxyRef)

        self.passthrough = functional.or_predicate(
            utils.FastTypePredicate(
                lambda cls: issubclass(cls, tuple(self.immutable_types))
            ).istypeof,
            self.is_bound,
        )

        passthrough_arg = utils.FastTypePredicate(
            lambda cls: issubclass(cls, tuple(self.immutable_types)) or issubclass(cls, tuple(self.patched_types))
        ).istypeof
        self.passthrough_call = functional.spread_and(passthrough_arg, starting = 1)

        self.serialize_ext_wrapped = functional.walker(
            when_instanceof(utils.ExternalWrapped, with_type_of(self.proxy_ref)))

        self.checkpoint = utils.noop

        self.descriptor_proxytype = functional.memoize_one_arg(self.descriptor_proxytype)

        self.counter = ThreadSafeCounter(initial = 0)

        self._thread_id = utils.ThreadLocal(None)
        self._thread_id.set(self.counter.next())
        self.thread_id = self._thread_id.get

    @property
    def ext_proxy(self):
        ext_passthrough = utils.FastTypePredicate(lambda cls: issubclass(cls, tuple(self.immutable_types) or issubclass(cls, utils.InternalWrapped))).is_typeof

        return functional.walker(
            functional.if_then_else(
                ext_passthrough,
                functional.identity,
                functional.if_then_else(
                    self.is_bound,
                    functional.identity,
                    functional.if_then_else(
                        self.is_patched_type,
                        functional.side_effect(self.async_new_patched),
                        proxy(functional.memoize_one_arg(self.ext_proxytype))))))

    @property 
    def int_proxy(self):
        int_passthrough = utils.FastTypePredicate(lambda cls: issubclass(cls, tuple(self.immutable_types) or issubclass(cls, utils.ExternalWrapped))).is_typeof

        return functional.walker(
            functional.if_then_else(
                int_passthrough,
                functional.identity,
                functional.if_then_else(
                    self.is_bound,
                    functional.identity,
                    functional.if_then_else(
                        self.is_patched_type,
                        functional.side_effect(self.async_new_patched),
                        functional.sequence(
                            proxy(functional.memoize_one_arg(self.int_proxytype)),
                            functional.side_effect(self.bind))))))


    def wrap_async(self, function):
        return self._wrapped_function(self.int_gateway, function)

    def _call_bind(self, obj):
        return self.bind(obj)

    def _call_async_new_patched(self, obj):
        return self.async_new_patched(obj)

    def run(self, function, *args, **kwargs):

        self.int_gateway.on_then = self.int_gateway_factory(
            gate = self.gate,
            int_proxy = self.int_proxy,
            ext_proxy = self.ext_proxy,
            hooks = CallHooks(
                on_call = self.primary_hooks.on_call if self.primary_hooks else None,
                on_result = self.secondary_hooks.on_result if self.secondary_hooks else None,
                on_error = self.secondary_hooks.on_error if self.secondary_hooks else None))
            
        self.ext_gateway.on_then = self.ext_gateway_factory(
            gate = self.gate,
            int_proxy = self.int_proxy,
            ext_proxy = self.ext_proxy,
            hooks = CallHooks(
                on_call = self.secondary_hooks.on_call if self.primary_hooks else None,
                on_result = self.primary_hooks.on_result if self.primary_hooks else None,
                on_error = self.primary_hooks.on_error if self.primary_hooks else None))

        def observe(function):
            return utils.observer(
                            on_call = functional.repeatedly(self.lifecycle_hooks.on_start) if self.lifecycle_hooks.on_start else None,
                            on_result = functional.repeatedly(self.lifecycle_hooks.on_end) if self.lifecycle_hooks.on_end else None,
                            on_error = functional.repeatedly(self.lifecycle_hooks.on_end) if self.lifecycle_hooks.on_end else None,
                            function = function)

        self.thread_wrapper = observe

        gc.collect()

        try:
            self.lifecycle_hooks.on_start()
            return function(*args, **kwargs)
        finally:            
            if callable(self.lifecycle_hooks.on_end):
                self.lifecycle_hooks.on_end()
                
            self.thread_wrapper = None

    @property
    def location(self):
        return self.gate.get()


    # Methods that must never be patched.  __new__ and __getattribute__
    # are fundamental to the object model; __del__ runs at GC time in
    # unpredictable contexts; __dict__ is a data descriptor needed by
    # the interpreter itself.
    _patch_type_blacklist = frozenset(['__new__', '__getattribute__', '__del__', '__dict__'])

    def _should_proxy_type(self, cls):
        """Decide whether values of *cls* need a dynamic proxy wrapper.

        Returns False for:
          - ``object`` itself (everything is an object, skip it)
          - any subclass of a type in ``immutable_types`` (e.g. int,
            str, bytes — their values pass through the boundary as-is)
          - built-in C data descriptors used by ``wrapped_member``
            (their raw descriptor object is part of the call shape)
          - any type already in ``patched_types`` (already handled)
        """
        return cls is not object and \
                not issubclass(cls, tuple(self.immutable_types)) and \
                cls not in (types.MemberDescriptorType, types.GetSetDescriptorType) and \
                cls not in self.patched_types

    def descriptor_proxytype(self, cls):
        slots = {}

        for name in ['__get__', '__set__', '__delete__']:
            if name in cls.__dict__:
                slots[name] = self._wrapped_function(self.ext_gateway, cls.__dict__[name])

        return type('FOOBAR', (utils.ExternalWrapped,), slots)

    def patch_type(self, cls, install_session=None):
        return patch_type(self, cls, install_session)

    def unpatch_type(self, cls):
        tracked_types = []
        tracked_wrapped = []

        def visit(target):
            if getattr(target, "__retrace_system__", None) is not self:
                return

            tracked_types.append(target)
            tracked_wrapped.extend(
                value
                for value in target.__dict__.values()
                if isinstance(value, utils._WrappedBase)
            )

            for subtype in target.__subclasses__():
                visit(subtype)

        visit(cls)
        _module_unpatch_type(cls)

        for target in tracked_types:
            stream.Binder.remove_bind_support(target)
            self.patched_types.discard(target)
            self.is_bound.discard(target)

        for wrapped in tracked_wrapped:
            self.is_bound.discard(wrapped)

        return cls

    def unpatch_types(self):
        for cls in sorted(tuple(self.patched_types), key=lambda cls: len(cls.__mro__), reverse=True):
            if cls in self.patched_types:
                self.unpatch_type(cls)

    def _wrapped_function(self, handler, target):
        wrapped = utils.wrapped_function(handler = handler, target = target)
        self.bind(wrapped)
        return wrapped

    def int_proxytype(self, cls):

        self.checkpoint(f'creating internal proxytype for {cls}')

        assert not self.is_patched_type(cls)
        assert not issubclass(cls, utils._WrappedBase)
        assert not cls.__module__.startswith('retracesoftware')
        assert not issubclass(cls, BaseException)

        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

        spec = {}

        def wrap(func): return self._wrapped_function(handler = self.int_gateway, target = func)
        
        for name in superdict(cls).keys():
            if name not in blacklist:
                try:
                    value = getattr(cls, name)
                except AttributeError:
                    # Some metatype attributes listed in the MRO dicts are not
                    # readable on the concrete class (for example `type` exposes
                    # `__abstractmethods__` here on 3.12). Skip those slots.
                    continue
                
                if utils.is_method_descriptor(value):
                    spec[name] = wrap(value) 

        spec['__getattr__'] = wrap(getattr)
        spec['__setattr__'] = wrap(setattr)
        
        if utils.yields_callable_instances(cls):
            spec['__call__'] = self.int_gateway

        spec['__class__'] = property(functional.constantly(cls))

        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__

        proxytype = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)

        self.bind(proxytype)

        patchtype(module = cls.__module__, name = cls.__qualname__, cls = proxytype)

        return proxytype

    def ext_proxytype(self, cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

        methods = [method for method in method_names(cls) if method not in blacklist]

        return self.ext_proxytype_from_spec(self, module = cls.__module__, name = cls.__qualname__, methods = methods)

    def install(self):
        import _thread
        import threading
        original_start_new_thread = _thread.start_new_thread

        _thread.start_new_thread = self.wrap_start_new_thread(_thread.start_new_thread)
        threading._start_new_thread = _thread.start_new_thread

        uninstall_hash_patching = install_hash_patching(self)

        def uninstall():
            _thread.start_new_thread = original_start_new_thread
            threading._start_new_thread = original_start_new_thread
            uninstall_hash_patching()

        return uninstall
