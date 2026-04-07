"""
System — the gate-based record/replay kernel.

System is the low-level core of the proxy.  It owns two thread-local
gates (_internal and _external) and uses them to intercept every call
that crosses the sandbox boundary.  Unlike the higher-level
RecordProxySystem/ReplayProxySystem (which also manage streams, thread
state, tracing, and serialisation), System is concerned *only* with
routing calls through the correct pipeline.

Architecture
------------

Two gates control all interception:

    _external   Intercepts calls from inside the sandbox to the outside
                world (int→ext).  Methods on a patched C/base type are
                routed through this gate.

    _internal   Intercepts callbacks from the outside world back into
                user code (ext→int).  Only methods on Python subclasses
                that *override* a base class method are routed through
                this gate (see "Why only overrides?" below).

Two additional lifecycle gates handle object allocation/binding:

    _async_new_patched
                Notified when a patched object is allocated while
                retrace is active.  The current record/replay path
                passes the newly allocated object directly.

    _bind       Generic binding hook used by stream backends.
                This is the protocol-level "attach an identity"
                operation.

All four gates are thread-local — each thread has its own executor
state, so recording on the main thread does not interfere with a
server thread's socket operations.

Call flow
---------

During record_context / replay_context the gates are loaded with
executors that form an adapter pipeline:

    1. ext_executor (set on _external gate)
       Handles int→ext calls.  Wraps the call with:
         - on_call   → observer (e.g. writer.sync)
         - proxy_input  → convert arguments from internal to external form
         - function  → actually execute (record) or read from stream (replay)
         - proxy_output → convert result from external to internal form
         - on_result → observer (e.g. writer.write_result)

    2. int_executor (set on _internal gate)
       Handles ext→int callbacks.  First checks whether we are already
       inside an external call (via _external.test(None)):
         - If _external.executor is None → nested internal call, just
           call through with functional.apply (passthrough).
         - Otherwise → genuine ext→int callback, wraps with the
           adapter pipeline in the reverse direction.

       NOTE — passthrough gap: when C base class code calls a Python
       override during an external call, the external gate has been
       cleared by apply_with(None).  The int_executor sees
       external=None → passthrough.  This means:
         (a) The callback is not recorded.
         (b) If the override makes outbound calls (e.g. super().recv()),
             they run with the external gate cleared — also unrecorded.
       The adapter branch (which restores the external gate) would be
       the correct path for these genuine ext→int callbacks.  Fixing
       this requires distinguishing "nested internal call" from
       "C code calling a Python override."

Gates as handlers
-----------------

Gates are directly callable: ``gate(target, *args, **kwargs)``.
When an executor is set, the call is forwarded to the executor.
When disabled (no executor, no default), the gate calls
``target(*args, **kwargs)`` — a transparent passthrough.  This
means gates can serve as handlers for ``wrapped_function`` and
``wrapped_member`` without any wrapper closure.

patch_type
----------

patch_type(cls) modifies a type in-place so that all its methods are
routed through the gates:

    - cls's own methods (from superdict) → wrapped as external
      (through _ext_handler, which checks whether the instance is
      already retraced before routing through the _external gate)

    - Existing Python subclasses of cls → methods that *override* a
      name from the base type's MRO are wrapped as internal (through
      _internal gate directly)

    - Future subclasses → __init_subclass__ is installed on cls so
      new subclasses are automatically patched as internal

    - tp_alloc → set_on_alloc installs _on_alloc, which either
      notifies the appropriate bind gate (inside a context) or leaves
      the object unbound (outside any context)

This means: if you patch_type(socket.socket), then socket.connect()
goes through _external, and if you define MySocket(socket.socket)
with a recv() override, that override goes through _internal.

Why only overrides?
~~~~~~~~~~~~~~~~~~~

C extension code can only dispatch to methods it knows about — names
defined in its own type slots or MRO.  If a Python subclass adds a
brand-new method (e.g. MySocket.send_with_retry()), no C code in the
base type will ever call it.  Only overrides of existing base methods
(recv, __lt__, compute, etc.) can be reached from C code as ext→int
callbacks.

Wrapping a method through the internal gate has a cost (closure
allocation, gate check on every call), so we skip methods that cannot
be callback targets.  The heuristic: only wrap subclass methods whose
name appears in the base type's superdict.

    socket.socket (patched)    MySocket(socket.socket)
    ─────────────────────      ─────────────────────────
    connect  → external        recv     → internal (override)
    recv     → external        process  → NOT wrapped (new method)
    send     → external        send_with_retry → NOT wrapped (new)
    close    → external

The ext→int callback scenario
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The key scenario that motivates internal wrapping:

    class MySocket(socket.socket):
        def recv(self, bufsize):           # internal (override)
            data = super().recv(bufsize)   # outbound ext call
            self.bytes_received += len(data)
            return data

    sock.makefile()                        # ext call (C code)
      └→ C code calls self.recv()          # dispatches to MySocket.recv
           └→ super().recv()               # should go through ext gate

Here makefile() is a C method that internally calls self.recv().
Because MySocket overrides recv, CPython dispatches to the Python
override — a genuine ext→int callback.  The internal wrapping on
recv ensures this call goes through the internal gate, where the
int_executor can record it and restore the external gate so that
super().recv() (an outbound call from within the callback) is also
properly intercepted.

Normalize (divergence detection)
---------------------------------

record_context and replay_context accept an optional ``normalize``
callable.  When set, every value that crosses the sandbox boundary
is normalized (reduced to a canonical, comparable form) and
checkpointed:

    - **External results** — the return value of every int→ext call.
    - **Internal results/errors** — the return value or exception from
      every ext→int callback.

During record, ``writer.checkpoint(normalize(value))`` stores the
normalized value alongside the normal recording.  During replay,
``reader.checkpoint(normalize(value))`` compares against the stored
value.  A mismatch means the user's code is not producing the same
values it produced during record — replay has diverged.

This is a guard rail, not a correctness mechanism.  Replay works
without it.  But when debugging a replay mismatch, normalize
pinpoints the first call where internal code deviated.

    def normalize(value):
        '''Reduce a value to something cheaply comparable.'''
        return (type(value).__name__, repr(value)[:100])

    with record_context(system, writer, normalize=normalize):
        ...

    with replay_context(system, reader, normalize=normalize):
        ...  # reader.checkpoint raises on mismatch

Usage
-----

    system = System()
    system.immutable_types.update({int, str, bytes, bool, float})
    system.patch_type(some_c_type)

    with record_context(system, writer):
        ...  # all calls on patched types are recorded

    with replay_context(system, reader):
        ...  # all calls on patched types are replayed from the stream
"""

from ast import Call
from typing import NamedTuple, Callable, Any
from contextlib import contextmanager
import retracesoftware.utils as utils
import retracesoftware.functional as functional
import types
import gc
from retracesoftware.proxy.proxytype import method_names, superdict
from retracesoftware.proxy.typeutils import WithoutFlags

from retracesoftware.proxy.proxytype import DynamicProxy
from retracesoftware.install.patcher import install_hash_patching
from retracesoftware.install.edgecases import patchtype

def proxy(proxytype_from):
    """Create a callable that wraps a value in a proxy type."""

    # cls = proxytype_from(type(value))
    # wrapped = utils.create_wrapped(cls, value)
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

def adapter(
    function,
    hooks,
    proxy_input,
    proxy_output,
    unproxy_input=functional.identity,
    unproxy_output=functional.identity,
):
    function = functional.mapargs(
        transform=functional.walker(unproxy_input),
        function = function,
    )
    
    function = functional.sequence(function, proxy_output)

    function = utils.observer(
        function=function,
        on_call = hooks.on_call,
        on_result = hooks.on_result,
        on_error = hooks.on_error,
    )

    function = functional.mapargs(
        starting=1,
        transform=functional.walker(proxy_input),
        function = function,
    )

    function = functional.sequence(function, unproxy_output)

    return function

class LifecycleHooks(NamedTuple):
    on_start: Callable[..., Any] | None = None
    on_end: Callable[..., Any] | None = None

class CallHooks(NamedTuple):
    """Lifecycle callbacks for one side of the proxy boundary."""

    on_call: Callable[..., Any] | None = None
    on_result: Callable[..., Any] | None = None
    on_error: Callable[..., Any] | None = None

class _PassthroughExternalCall(Exception):
    """Signal that an external call should bypass retrace and run live."""

def get_all_subtypes(cls):
    """Recursively find all subtypes of a given class."""

    subclasses = set(cls.__subclasses__())
    for subclass in cls.__subclasses__():
        subclasses.update(get_all_subtypes(subclass))
    return subclasses

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
            spec[method] = system._wrapped_function(handler = system._ext_handler, target = proxy(method))
        
        proxytype = type(name, (utils.ExternalWrapped, DynamicProxy,), spec)

        patchtype(module = module, name = name, cls = proxytype)
        system.bind(proxytype)
        system.bind(system.proxy_ref(proxytype))

        return proxytype

def _is_patch_generated_init_subclass(value):
    if not isinstance(value, classmethod):
        return False

    func = value.__func__
    code = getattr(func, "__code__", None)
    if code is None or func.__name__ != "init_subclass":
        return False

    freevars = set(code.co_freevars)
    return {"self", "proxy_attrs", "subtype_attrs"} <= freevars

_MISSING = object()

def _restore_attr(target, name, original):
    if original is _MISSING:
        if name in target.__dict__:
            delattr(target, name)
    else:
        setattr(target, name, original)

def _unwrap_patched_attr(cls, name, value):
    target = utils.unwrap(value)

    for base in cls.__mro__[1:]:
        if base.__dict__.get(name) is target:
            delattr(cls, name)
            return value

    setattr(cls, name, target)
    return value

def _unpatch_type_one(
    cls,
    *,
    original_attrs=None,
    original_init_subclass=_MISSING,
    original_retrace_system=_MISSING,
    original_retrace=_MISSING,
):
    with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
        utils.clear_on_alloc(cls)

        if original_attrs is None:
            for name, value in tuple(cls.__dict__.items()):
                if isinstance(value, utils._WrappedBase):
                    _unwrap_patched_attr(cls, name, value)
        else:
            for name, original in reversed(list(original_attrs.items())):
                _restore_attr(cls, name, original)

        if original_init_subclass is _MISSING:
            init_subclass = cls.__dict__.get("__init_subclass__")
            if _is_patch_generated_init_subclass(init_subclass):
                delattr(cls, "__init_subclass__")
        else:
            _restore_attr(cls, "__init_subclass__", original_init_subclass)

        if original_retrace_system is _MISSING:
            if "__retrace_system__" in cls.__dict__:
                delattr(cls, "__retrace_system__")
        else:
            _restore_attr(cls, "__retrace_system__", original_retrace_system)

        if original_retrace is _MISSING:
            if "__retrace__" in cls.__dict__:
                delattr(cls, "__retrace__")
        else:
            _restore_attr(cls, "__retrace__", original_retrace)

def unpatch_type(cls):
    assert isinstance(cls, type)

    system = getattr(cls, "__retrace_system__", None)

    for subtype in tuple(cls.__subclasses__()):
        if getattr(subtype, "__retrace_system__", None) is system:
            unpatch_type(subtype)

    _unpatch_type_one(cls)

    return cls

_module_unpatch_type = unpatch_type

class System:
    """Gate-based record/replay kernel.

    System is the minimal, self-contained engine that makes record and
    replay work.  It owns the gates, patches types in-place, and
    provides context managers that wire up the full adapter pipeline.

    Public API
    ----------
    patch_type(cls)       Patch a C/base type so its methods go through
                          the gates.  Python subclasses are automatically
                          patched as internal.

    context(...)          Build a runnable context from a frozen set of
                          handler values.

    immutable_types       Set of types that should never be proxied (their
                          values pass through the boundary as-is).

    Attributes (private — not part of the public API)
    -------------------------------------------------
    _internal             Gate for ext→int callbacks.
    _external             Gate for int→ext calls.
    _new_patched          Gate notified on patched object allocation.
    _bind                 Generic stream/object binding hook.
    _in_sandbox()         True when the external gate has an executor
                          (i.e. we are inside a record/replay context).
    _out_sandbox()        True when the internal gate has an executor.
    """

    def _observeable_thread_function(self, function):
        def on_start(*args, **kwargs):
            if hasattr(self, "on_start") and callable(self.on_start):
                self.on_start()    

        def on_end(*args, **kwargs):
            if hasattr(self, "on_end") and callable(self.on_end):
                self.on_end()
            
        return utils.observer(
            function = function,
            on_call = on_start, 
            on_result = on_end,
            on_error = on_end)

    def wrap_start_new_thread(self, original_start_new_thread):
        """Wrap ``start_new_thread`` so child threads inherit active retrace context.

        The wrapper captures ``self.current_context`` from the parent thread
        at spawn time and, when retrace is active, rewrites the child target
        so it enters that same context before executing user code.
        """
        def wrap_thread_function(function):
            if self.enabled():
                return self.thread_wrapper(function)
            else:
                return function

        return functional.positional_param_transform(
            function=original_start_new_thread,
            index=0,
            transform=wrap_thread_function,
        )

    def patch_function(self, fn):
        """Return a wrapper that routes *fn* through the external gate.

        When no context is active (``executor is None``), the wrapper
        calls *fn* directly — near-zero overhead.  Inside
        ``record_context`` or ``replay_context`` the call goes through
        the adapter pipeline, just like a method on a patched type.

        Uses ``Gate.bind`` at the C level for maximum performance.

        Use this for standalone module-level functions (e.g.
        ``time.time``, ``os.getpid``) that need to be recorded and
        replayed.
        """
        self.bind(fn)
        return self._external.bind(fn)
        # wrapped = utils.wrapped_function(handler = self._ext_handler, target = fn)
        # self.bind(wrapped)
        # return wrapped

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

    def disable_for(self, function):
        """Return a callable that runs *function* with both gates disabled.

        This is used when the system needs to call its own internal
        helpers (e.g. proxytype factories) without triggering the
        adapter pipeline.  The returned callable:

            1. Temporarily sets _external.executor to None
            2. Temporarily sets _internal.executor to None
            3. Calls function(*args, **kwargs)
            4. Restores both executors

        The ``apply_with(None)`` calls are nested so that each one
        treats the *next* step as the function to run, preserving the
        original arguments for *function*.

        ``function`` may itself be a ``wrapped_function``. In that case we
        still want to execute its underlying target rather than re-entering
        the wrapper/handler path with both gates cleared, so the disabled
        call goes through ``self.fallback`` rather than invoking *function*
        directly.
        """
        apply_ext = self._external.apply_with(None)
        apply_int = self._internal.apply_with(None)

        return functional.partial(
            apply_ext,
            functional.partial(apply_int, self.fallback),
            function,
        )

    def create_dispatch(self, disabled, external, internal):
        return functional.cond(
            self._external.is_set, external,
            self._internal.is_set, internal,
            disabled)

    def create_gate(self, disabled, external, internal):
        """Create a ``Gate`` that dispatches based on the system's state.

        Returns a new ``Gate`` whose default executor is a
        ``functional.cond`` that checks the system's primary gates:

        - External gate active → call *external*
        - Internal gate active → call *internal*
        - Neither active       → call *disabled*

        The returned gate is callable and requires no manual
        ``set``/``disable`` — it piggybacks on the primary gates'
        state automatically.

        Parameters
        ----------
        disabled : callable
            Called when neither gate is active (no record/replay
            context).
        external : callable
            Called when the external gate has an executor (inside
            a record/replay context, processing an int→ext call).
        internal : callable
            Called when the internal gate has an executor (inside
            a record/replay context, processing an ext→int callback).
        """
        return utils.Gate(self.create_dispatch(disabled, external, internal))

    def __init__(self, on_bind = None) -> None:
        # ── Primary gates ──────────────────────────────────────────
        #
        # _internal: ambient "retrace is enabled on this thread" gate.
        #   It is installed for the whole lifetime of a record/replay
        #   context and handles ext→int callbacks (methods on Python
        #   subclasses of the patched type).
        #
        # _external: phase gate for int→ext calls (methods on the
        #   patched C/base type).  During normal sandbox execution it
        #   is set alongside _internal.  While the external call body
        #   itself runs, apply_with(None) temporarily clears only this
        #   gate, which makes "internal" and "external" phases cheap
        #   to distinguish without mutating _internal.
        #
        # Both gates are thread-local: each thread has its own
        # executor slot, so recording on one thread does not affect
        # another.

        self.passthrough_proxyref = False
        self.ext_execute = utils.try_unwrap_apply
        self.int_execute = utils.try_unwrap_apply

        self.primary_hooks = CallHooks(
            on_call = None,
            on_result = None,
            on_error = None,
        )
        self.secondary_hooks = CallHooks(
            on_call = None,
            on_result = None,
            on_error = None,
        )

        self.lifecycle_hooks = LifecycleHooks(
            on_start = None,
            on_end = None,
        )

        self.patched_types = set()
        self.immutable_types = set()

        self._internal = utils.Gate()
        self._external = utils.Gate()
        

        # ── Binding / allocation gates ────────────────────────────
        #
        # These are gates whose default state is noop (not None).
        # They are called from _on_alloc when a patched type is
        # instantiated, to notify the writer/reader that a new object
        # has entered the boundary.
        #
        # _async_new_patched: allocation-origin notification.  Receives the
        #               allocated object itself; backends can recover
        #               the concrete type from the live object.
        # _bind:        generic binding hook kept separate from
        #               allocation-origin semantics.
        # self._async_new_patched = utils.Gate(default = utils.noop)
        # self._bind = utils.Gate(default = utils.noop)

        # ── Bound / unretraced sets ───────────────────────────────
        #
        # _bound tracks every object/type that was seen by bind/async_new_patched.
        # It uses a native hybrid weak set: weakrefable objects auto-evict,
        # others are held strongly for the lifetime of the System.
        self.is_bound = utils.WeakSet()
        
        # def on_async_new_patched(obj):
        #     if self.primary_hooks:
        #         # this doesn't trigger the call on record, this is for replay
        #         self.primary_hooks.on_call(utils.create_stub_object, type(obj))
                        
        #     on_bind(obj)

        #     if self.secondary_hooks.on_result:
        #         self.secondary_hooks.on_result(obj)

        self.bind = utils.runall(self.is_bound.add, on_bind)

        # self.async_new_patched = utils.runall(self.is_bound.add, on_async_new_patched)
        self.async_new_patched = utils.noop

        self.create_stub_object = utils.runall(self.is_bound.add, utils.create_stub_object)
        # Execute wrapped callables via their underlying target while leaving
        # plain callables on the normal fast path.
        self.fallback = functional.mapargs(
            transform = functional.walker(utils.try_unwrap),
            function = functional.apply)

        # Return True if *obj* is bound or is a dynamic proxy wrapper.
        self.is_retraced = functional.or_predicate(self.is_bound, utils.is_wrapped)
        self.is_patched = lambda obj: (
            (isinstance(obj, type) and obj in self.patched_types)
            or type(obj) in self.patched_types
        )

        self.is_patched_type = utils.FastTypePredicate(lambda cls: cls in self.patched_types).istypeof

        # def bind_unbound(obj):
        #     writer.async_new_patched(type(obj))
        #     remember_bind(obj)


        # ── Sandbox predicates ─────────────────────────────────────
        #
        # _in_sandbox(): True when the external phase gate is set.
        #   In practice this means we are in the normal retraced
        #   "internal" phase of a record/replay context.
        #
        # _out_sandbox(): True when the ambient internal gate is set.
        #   This means retrace is enabled on the current thread,
        #   including both the internal phase and the temporary
        #   external-call phase.
        #
        # Both are bound methods of Gate.is_set (C-level, fast).
        self._in_sandbox = self._external.is_set
        self._out_sandbox = self._internal.is_set

        # ── Method handlers ────────────────────────────────────────
        #
        # Gates are directly callable as handlers:
        #   gate(target, *args, **kwargs) → executor(target, *args, **kwargs)
        #     when an executor is set, or target(*args, **kwargs)
        #     when disabled (passthrough).
        #
        # _ext_handler is used by patch_type for methods on patched
        # base types (int→ext calls). Calls always enter the external
        # gate, but mixed live/retraced arguments can raise
        # _PassthroughExternalCall during input transformation. In that
        # case we fall back to the real target locally.
        #
        # _int_handler is the raw _internal gate.  Subclass methods
        # (ext→int callbacks) dispatch to the executor when active, or
        # pass through when disabled.
        self._ext_handler = functional.catch_exception(
            self.create_dispatch(
                disabled=self.fallback,
                external=self._external,
                internal=self.fallback,
            ),
            _PassthroughExternalCall,
            self.fallback,
        )

        self._int_handler = self.create_dispatch(
                disabled=self.fallback,
                external=self.fallback,
                internal=self._internal,
            )

        # Internal overrides always route through the internal gate while
        # retrace is active. When retrace is disabled, ``self._internal``
        # naturally passthroughs to the original target.        self._int_handler = self._internal
        self._override_handler = self.create_dispatch(
            disabled=self.fallback,
            # Direct in-sandbox calls to Python overrides should behave like
            # normal Python method calls. Only true outside->inside callbacks
            # (which run while the external gate is temporarily cleared) should
            # route through the internal gate.
            external=self.fallback,
            internal=self._int_handler,
        )

        # ── Allocation hook ────────────────────────────────────────
        #
        # _on_alloc is installed on the patched type family.
        # Objects created outside any context remain live/unbound.
        #
        # The allocation gate (_async_new_patched) is called directly —
        # gate(obj) dispatches to the executor when set, or to the
        # default (utils.noop) when no context is active.
        #
        # The logic:
        #   - When the external gate is active: _bind(obj), because the
        #     concrete object already exists and only needs a stream
        #     identity.
        #   - When the internal gate is active: emit _async_new_patched(obj)
        #     first, then bind the concrete object. This is the
        #     out-of-sandbox allocation path during retrace: replay
        #     needs the type signal in order to materialize the object,
        #     and both record/replay still need the normal binding
        #     lifecycle for the created instance.
        #   - Otherwise: utils.noop keeps the object unbound, so later
        #     patched method calls passthrough.
        self._on_alloc = functional.cond(
            self._external.is_set, self.bind,
            self._internal.is_set, self.async_new_patched,
            utils.noop)

        # self._on_alloc = functional.cond(
        #     self._external.is_set, self.bind,
        #     utils.noop)

        self.ext_proxytype_from_spec = self.wrap_async(_ext_proxytype_from_spec)
        
        self.bind(self)

        self.proxy_ref = functional.memoize_one_arg(ProxyRef)

        self.passthrough = functional.or_predicate(
            utils.FastTypePredicate(
                lambda cls: issubclass(cls, tuple(self.immutable_types))
            ).istypeof,
            self.is_bound,
        )

        self.passthrough_call = functional.spread_and(self.passthrough)

        self.ext_passthrough = functional.or_predicate(
            self.passthrough,
            functional.isinstanceof(utils.InternalWrapped),
        )

        self.int_passthrough = functional.or_predicate(
            self.passthrough,
            functional.isinstanceof(utils.ExternalWrapped),
        )

        self.serialize_ext_wrapped = functional.walker(
            when_instanceof(utils.ExternalWrapped, with_type_of(self.proxy_ref)))

        self.unproxy_ext = functional.walker(
            when_instanceof(utils.ExternalWrapped, utils.unwrap))

        self.unproxy_int = functional.walker(
            when_instanceof(utils.InternalWrapped, utils.unwrap))

        self.checkpoint = utils.noop

        self.descriptor_proxytype = functional.memoize_one_arg(self.descriptor_proxytype)        
        self.ext_proxy = proxy(functional.memoize_one_arg(self.ext_proxytype))

    def wrap_async(self, function):
        return self._wrapped_function(self._internal, function)

    def _ext_proxy(self):
        ext_leaf_proxy = \
            functional.if_then_else(
                self.ext_passthrough,
                functional.identity,
                functional.if_then_else(
                    self.is_patched_type,
                    functional.side_effect(self.async_new_patched),
                    self.ext_proxy))

        if not self.passthrough_proxyref:
            ext_leaf_proxy = \
                functional.if_then_else(
                    functional.isinstanceof(ProxyRef),
                    lambda ref: ref(),
                    ext_leaf_proxy)

        return functional.walker(ext_leaf_proxy)

    def _int_proxy(self):

        create_proxy = functional.sequence(
            proxy(functional.memoize_one_arg(self.int_proxytype)),
            functional.side_effect(self.bind))

        leaf_int_proxy = functional.if_then_else(
            self.int_passthrough,
            functional.identity,
            functional.if_then_else(
                self.is_patched_type,
                functional.side_effect(self.async_new_patched),
                create_proxy))
            
        return functional.if_then_else(
            self.int_passthrough,
            functional.identity,
            functional.walker(leaf_int_proxy))

    def ext_executor(self, *, int_proxy, ext_proxy, hooks):
        
        with_generic_ext_result = \
            functional.sequence(
                ext_proxy,
                functional.side_effect(
                      functional.sequence(
                          self.serialize_ext_wrapped,
                          hooks.on_result)),
                self.unproxy_int)

        if self.passthrough_proxyref:
            with_ext_result = functional.if_then_else(
                self.passthrough,
                functional.side_effect(hooks.on_result),
                with_generic_ext_result)
        else:
            with_ext_result = with_generic_ext_result

        _run_external = functional.partial(
            self._external.apply_with(None),
            self.ext_execute,
        )

        external_executor = functional.sequence(_run_external, with_ext_result)

        def observe(function):
            return utils.observer(
                on_call = hooks.on_call,
                on_error = hooks.on_error,
                function = function)

        with_complex_ext_call = \
            functional.mapargs(
                starting = 1,
                transform = functional.if_then_else(
                    self.int_passthrough,
                    functional.identity,
                    int_proxy),
                function = observe(
                    functional.mapargs(
                        starting = 1,
                        function = external_executor,
                        transform = self.unproxy_ext)))

        return functional.if_then_else(
            self.passthrough_call,
            observe(external_executor),
            with_complex_ext_call)

    def int_executor(self, *, int_proxy, ext_proxy, hooks, external_executor):
        return functional.mapargs(
            starting = 1,
            transform = functional.if_then_else(
                self.ext_passthrough,
                functional.identity,
                ext_proxy),
            function = utils.observer(
                on_call = hooks.on_call,
                on_error = hooks.on_error,
                function = functional.mapargs(
                    starting = 1,
                    function = functional.sequence(
                        functional.partial(
                            self._external.apply_with(external_executor),
                            self.int_execute),
                        int_proxy,
                        functional.side_effect(
                            functional.sequence(
                                self.serialize_ext_wrapped,
                                hooks.on_result)),
                        self.unproxy_ext,
                    ),
                    transform = self.unproxy_int)))

    def run(self, function, *args, **kwargs):
        int_proxy = self._int_proxy()
        ext_proxy = self._ext_proxy()

        external_executor = self.ext_executor(
            int_proxy = int_proxy,
            ext_proxy = ext_proxy,
            hooks = CallHooks(
                on_call = self.secondary_hooks.on_call if self.secondary_hooks else None,
                on_result = self.primary_hooks.on_result if self.primary_hooks else None,
                on_error = self.primary_hooks.on_error if self.primary_hooks else None))

        internal_executor = self.int_executor(
            int_proxy = int_proxy,
            ext_proxy = ext_proxy,
            hooks = CallHooks(
                on_call = self.primary_hooks.on_call if self.primary_hooks else None,
                on_result = self.secondary_hooks.on_result if self.secondary_hooks else None,
                on_error = self.secondary_hooks.on_error if self.secondary_hooks else None),
            external_executor = external_executor)

        def with_gate(gate, executor,function):
            return functional.partial(gate.apply_with(executor), function)

        def observe(function):
            return utils.observer(
                            on_call = functional.repeatedly(self.lifecycle_hooks.on_start) if self.lifecycle_hooks.on_start else None,
                            on_result = functional.repeatedly(self.lifecycle_hooks.on_end) if self.lifecycle_hooks.on_end else None,
                            on_error = functional.repeatedly(self.lifecycle_hooks.on_end) if self.lifecycle_hooks.on_end else None,
                            function = function)

        self.thread_wrapper = lambda function: with_gate(
            self._external,
            external_executor,
            with_gate(self._internal, internal_executor, observe(function)),
        )

        self._internal.executor = internal_executor
        self._external.executor = external_executor

        gc.collect()

        try:
            self.lifecycle_hooks.on_start()
            return function(*args, **kwargs)
        finally:            
            if callable(self.lifecycle_hooks.on_end):
                self.lifecycle_hooks.on_end()
                
            self.thread_wrapper = None
            self._internal.executor = None
            self._external.executor = None

    # @contextmanager
    # def context(self):        
    #     int_proxy = self._int_proxy()
    #     ext_proxy = self._ext_proxy()

    #     external_executor = self.ext_executor(
    #         int_proxy = int_proxy,
    #         ext_proxy = ext_proxy,
    #         hooks = CallHooks(
    #             on_call = self.secondary_hooks.on_call if self.secondary_hooks else None,
    #             on_result = self.primary_hooks.on_result if self.primary_hooks else None,
    #             on_error = self.primary_hooks.on_error if self.primary_hooks else None))

    #     internal_executor = self.int_executor(
    #         int_proxy = int_proxy,
    #         ext_proxy = ext_proxy,
    #         hooks = CallHooks(
    #             on_call = self.primary_hooks.on_call if self.primary_hooks else None,
    #             on_result = self.secondary_hooks.on_result if self.secondary_hooks else None,
    #             on_error = self.secondary_hooks.on_error if self.secondary_hooks else None),
    #         external_executor = external_executor)

    #     def with_gate(gate, executor,function):
    #         return functional.partial(gate.apply_with(executor), function)

    #     self.thread_wrapper = lambda function: with_gate(
    #         self._external,
    #         external_executor,
    #         with_gate(self._internal, internal_executor, observe(function)),
    #     )

    #     def observe(function):
    #         return utils.observer(
    #                         on_call = functional.repeatedly(self.lifecycle_hooks.on_start),
    #                         on_result = functional.repeatedly(self.lifecycle_hooks.on_end),
    #                         on_error = functional.repeatedly(self.lifecycle_hooks.on_end),
    #                         function = function)

    #     self._internal.executor = internal_executor
    #     self._external.executor = external_executor

    #     gc.collect()

    #     try:
    #         self.lifecycle_hooks.on_start()
    #         yield
    #     finally:            
    #         self.lifecycle_hooks.on_end()
    #         self.thread_wrapper = None
    #         self._internal.executor = None
    #         self._external.executor = None
        
    def enabled(self):
        return self._external.is_set or self._internal.is_set

    @property
    def location(self):
        """Current execution location relative to the retrace boundary.

        Returns:
            'disabled' when no gate executors are active.
            'internal' when running in retraced/internal code.
            'external' when running in an external call body.
        """
        if not self._out_sandbox():
            return 'disabled'
        if self._in_sandbox():
            return 'internal'
        return 'external'

    @property
    def ext_handler(self):
        return self._ext_handler

    @property
    def int_handler(self):
        return self._internal

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
                slots[name] = self._wrapped_function(self._ext_handler, cls.__dict__[name])

        return type('FOOBAR', (utils.ExternalWrapped,), slots)

    def patch_type(self, cls, install_session=None):
        """Patch *cls* in-place so its methods route through the gates.

        This is the central operation of the system.  After calling
        ``patch_type(cls)``:

        1. **External methods** — every callable and descriptor on
           *cls* (collected via ``superdict`` which walks the MRO) is
           replaced with a wrapper that routes through ``_external``.
           These are int→ext calls: code inside the sandbox calling a
           method on an outside-world type.

        2. **Allocation hook** — ``set_on_alloc`` installs ``_on_alloc``
           on the type's ``tp_alloc`` slot.  Whenever a new instance of
           *cls* (or a subclass) is created, the appropriate bind gate
           is notified.

        3. **Subclass patching** — if *cls* is extendable (can have
           Python subclasses), all *existing* subclasses are found via
           ``get_all_subtypes`` and patched as internal.  A custom
           ``__init_subclass__`` is installed on *cls* so that *future*
           subclasses are also patched automatically.

           Only subclass methods that **override** a name from the
           base type's MRO are wrapped.  C extension code can only
           dispatch to methods it knows about, so a brand-new method
           on the subclass can never be an ext→int callback target.
           Skipping non-overrides avoids unnecessary wrapping overhead.

           The wrapped overrides route through ``_internal`` (the
           ext→int gate).  This is how callbacks work: when C code in
           a base class method calls ``self.method()`` and the Python
           subclass overrides that method, the call goes through the
           internal gate where it can be recorded and where the
           external gate can be restored for any outbound calls the
           override makes (e.g. ``super().method()``).

        4. **Bind notification** — ``_bind(cls)`` is called to notify
           the current bind executor (if any) that a new type has
           entered the system.

        Parameters
        ----------
        cls : type
            The type to patch.  Must not be a BaseException subclass
            (exceptions are not proxied).  Must not already be in
            ``patched_types``.

        Returns
        -------
        cls : type
            The same type, now patched in-place.

        Example
        -------
            import _socket
            system = System()
            system.immutable_types.update({int, str, bytes, bool})
            system.patch_type(_socket.socket)
            # Now socket.connect(), socket.recv(), etc. all go through
            # the external gate when an executor is set.
            # A Python subclass that overrides recv() will have that
            # override routed through the internal gate.
        """

        assert isinstance(cls, type)
        assert not issubclass(cls, BaseException)

        existing = getattr(cls, "__retrace_system__", None)
        if existing is not None and existing is not self:
            raise RuntimeError(
                f"patch_type: {cls.__qualname__} is already patched by another System instance"
            )

        assert cls not in self.patched_types

        alloc_patch_undo = None
        patched_attrs = {}
        patched_subtypes = []
        subtype_alloc_undos = []
        subtype_attrs = {}
        original_init_subclass = cls.__dict__.get("__init_subclass__", _MISSING)
        original_retrace = cls.__dict__.get("__retrace__", _MISSING)
        original_retrace_system = cls.__dict__.get("__retrace_system__", _MISSING)
        bound_types = []

        def bind_patched_type(target):
            self.bind(target)
            bound_types.append(target)


        def proxy_attrs(target_cls, attr_dict, handler, originals):
            blacklist = self._patch_type_blacklist

            def proxy_function(func):
                return self._wrapped_function(handler=handler, target=func)

            def proxy_member(member):
                return self.descriptor_proxytype(type(member))(member)

            for name, value in attr_dict.items():
                if name in blacklist:
                    continue

                if name not in originals:
                    originals[name] = getattr(target_cls, name)

                def with_proxied(proxied):
                    setattr(target_cls, name, proxied)
                    if install_session is not None:
                        install_session.register_wrapped_attr(
                            owner=target_cls,
                            name=name,
                            target=value,
                            wrapped=proxied,
                        )

                if type(value) in [types.MemberDescriptorType, types.GetSetDescriptorType]:
                    with_proxied(proxy_member(value))
                elif callable(value) and not isinstance(value, type):
                    with_proxied(proxy_function(value))

        try:
            with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
                alloc_patch_undo = utils.set_on_alloc(cls, self._on_alloc)
                self.patched_types.add(cls)

                base_methods = superdict(cls)
                proxy_attrs(cls, attr_dict=base_methods, handler=self._ext_handler, originals=patched_attrs)

                cls.__retrace_system__ = self

                if utils.is_extendable(cls):
                    base_method_names = frozenset(base_methods.keys())

                    # Bind the base before retro-patching existing subclasses so
                    # replay sees the same binding order as record-time patching
                    # followed by later subclass definition.
                    bind_patched_type(cls)

                    def init_subclass(subtype, patch_alloc=True, **kwargs):
                        self.patched_types.add(subtype)
                        patched_subtypes.append(subtype)
                        bind_patched_type(subtype)

                        if patch_alloc:
                            alloc_undo = utils.set_on_alloc(subtype, self._on_alloc)
                            subtype_alloc_undos.append(alloc_undo)

                        overrides = {
                            name: value
                            for name, value in subtype.__dict__.items()
                            if name in base_method_names
                        }
                        originals = subtype_attrs.setdefault(subtype, {})
                        proxy_attrs(
                            subtype,
                            attr_dict=overrides,
                            handler=self._override_handler,
                            originals=originals,
                        )

                    cls.__init_subclass__ = classmethod(init_subclass)

                    for subtype in get_all_subtypes(cls):
                        with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                            init_subclass(subtype, patch_alloc=False)

                cls.__retrace__ = self

            if cls not in bound_types:
                bind_patched_type(cls)
        except Exception:
            for undo in reversed(subtype_alloc_undos):
                undo()

            if alloc_patch_undo is not None:
                alloc_patch_undo()

            for subtype in reversed(patched_subtypes):
                _unpatch_type_one(
                    subtype,
                    original_attrs=subtype_attrs.get(subtype, {}),
                )
                self.patched_types.discard(subtype)

            _unpatch_type_one(
                cls,
                original_attrs=patched_attrs,
                original_init_subclass=original_init_subclass,
                original_retrace_system=original_retrace_system,
                original_retrace=original_retrace,
            )

            for bound_type in reversed(bound_types):
                self.is_bound.discard(bound_type)

            self.patched_types.discard(cls)
            raise

        return cls

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
            self.patched_types.discard(target)
            self.is_bound.discard(target)

        for wrapped in tracked_wrapped:
            self.is_bound.discard(wrapped)

        return cls

    def unpatch_types(self):
        for cls in sorted(tuple(self.patched_types), key=lambda cls: len(cls.__mro__), reverse=True):
            if cls in self.patched_types:
                self.unpatch_type(cls)

    def _throw_passthrough(self, _value):
        raise _PassthroughExternalCall

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

        def wrap(func): return self._wrapped_function(handler = self._int_handler, target = func)
        
        for name in superdict(cls).keys():
            if name not in blacklist:
                try:
                    value = getattr(cls, name)
                except AttributeError:
                    # Some metatype attributes listed in the MRO dicts are not
                    # readable on the concrete class (for example `type` exposes
                    # `__abstractmethods__` here on 3.12). Skip those slots.
                    continue
                
                # if is_descriptor(value):
                if utils.is_method_descriptor(value):
                    spec[name] = wrap(value) 

        spec['__getattr__'] = wrap(getattr)
        spec['__setattr__'] = wrap(setattr)
        
        if utils.yields_callable_instances(cls):
            spec['__call__'] = self._int_handler

        spec['__class__'] = property(functional.constantly(cls))

        spec['__name__'] = cls.__name__
        spec['__module__'] = cls.__module__

        proxytype = type(cls.__name__, (utils.InternalWrapped, DynamicProxy), spec)
        self.bind(proxytype)
        return proxytype

    # have the ext proxytype as a tracked external method
    # it takes the shape of the class, inside we bind the generated proxytype
    # this mean when we call the ext proxytype from inside the system, its tracked
    # as an async call, which will be replayed and bound. Dont need any stub refs.

    def ext_proxytype(self, cls):
        blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

        methods = [method for method in method_names(cls) if method not in blacklist]

        return self.ext_proxytype_from_spec(self, module = cls.__module__, name = cls.__qualname__, methods = methods)
        # return self._internal(System.ext_proxytype_from_spec, self, 
        #                       module = cls.__module__, name = cls.__qualname__, methods = methods)

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
