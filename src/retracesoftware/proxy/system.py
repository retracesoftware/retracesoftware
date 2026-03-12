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

    _new_patched
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
      (through _ext_handler, which checks _unretraced before routing
      through the _external gate)

    - Existing Python subclasses of cls → methods that *override* a
      name from the base type's MRO are wrapped as internal (through
      _internal gate directly)

    - Future subclasses → __init_subclass__ is installed on cls so
      new subclasses are automatically patched as internal

    - tp_alloc → set_on_alloc installs _on_alloc, which either
      notifies the appropriate bind gate (inside a context) or adds
      the object to _unretraced (outside any context)

    - Existing live instances of the patched type family are swept into
      _unretraced immediately after patching so pre-patch runtime state
      stays live on both record and replay

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

    with system.record_context(writer, normalize=normalize):
        ...

    with system.replay_context(reader, normalize=normalize):
        ...  # reader.checkpoint raises on mismatch

Usage
-----

    system = System()
    system.immutable_types.update({int, str, bytes, bool, float})
    system.patch_type(some_c_type)

    with system.record_context(writer):
        ...  # all calls on patched types are recorded

    with system.replay_context(reader):
        ...  # all calls on patched types are replayed from the stream
"""

import gc
import retracesoftware.utils as utils
import retracesoftware.functional as functional
from types import SimpleNamespace
from typing import Callable
from retracesoftware.proxy.typeutils import WithoutFlags
from retracesoftware.proxy.stubfactory import StubRef

import _thread
from retracesoftware.proxy.proxytype import dynamic_int_proxytype, dynamic_proxytype, superdict
import types


class _ReplayStubFactory:
    """Create lightweight stub instances from StubRef metadata.

    During replay, proxy-requiring objects on the tape are stored as
    StubRefs (type metadata captured at record time).  This factory
    reconstructs a raw (non-Wrapped) stub instance that mirrors the
    original type's method interface.

    The stub is NOT a Wrapped instance, so ``maybe_proxy`` in
    ``proxy_output`` wraps it in a fresh DynamicProxy whose methods
    route through the replay gate — reading results from the stream
    instead of executing real code.

    Follows the same pattern as the old ``StubFactory`` but without
    ``thread_state`` / ``next_result`` dependencies — the DynamicProxy
    layer handles stream reads.
    """
    def __init__(self):
        self._cache = {}

    def __call__(self, spec):
        if spec not in self._cache:
            self._cache[spec] = self._create_stubtype(spec)
        stubtype = self._cache[spec]
        return stubtype.__new__(stubtype)

    @staticmethod
    def _create_stubtype(spec):
        slots = {'__module__': spec.module}
        for method in spec.methods:
            def _noop(self, *args, **kwargs):
                pass
            _noop.__name__ = method
            slots[method] = _noop
        return type(spec.name, (object,), slots)

class _GateContext:
    """Reusable, thread-safe context manager for gate executors.

    Each thread that enters this context gets its own saved state,
    so the same context can be entered concurrently by multiple
    threads (main + children) without conflicts.
    """
    __slots__ = ('_system', '_kwargs', '_saved')

    def __init__(self, system, **kwargs):
        self._system = system
        self._kwargs = kwargs
        self._saved = _thread._local()

    def __enter__(self):
        saved = {}
        for key in self._kwargs:
            saved[key] = getattr(self._system, key).executor
        self._saved.state = saved
        for key, value in self._kwargs.items():
            setattr(getattr(self._system, key), 'executor', value)
        return self._system

    def __exit__(self, *exc):
        for key, value in self._saved.state.items():
            setattr(getattr(self._system, key), 'executor', value)
        return False


def get_all_subtypes(cls):
    """Recursively find all subtypes of a given class."""
    subclasses = set(cls.__subclasses__())
    for subclass in cls.__subclasses__():
        subclasses.update(get_all_subtypes(subclass))
    return subclasses

def _run_with_replay(ext_runner, replay_materialize = None, materialize = None):
    """Return a callable matching apply_with's signature: (fn, *args, **kwargs).

    During replay, the real external function is never called.  Instead,
    ext_runner() reads the next recorded result from the stream and
    returns it directly.  fn, args, and kwargs are ignored.
    """
    replay_materialize = replay_materialize or frozenset()

    def replay_fn(fn, *args, **kwargs):
        recorded = ext_runner()

        key = utils.unwrap(fn) if utils.is_wrapped(fn) else fn
        if key in replay_materialize:
            if materialize is None:
                raise RuntimeError("replay materialization requested without materializer")
            return materialize(key, *args, **kwargs)

        return recorded
    return replay_fn

def adapter(function,
            proxy_input,
            proxy_output,
            on_call = None,
            on_result = None,
            on_error = None):
    """Build an adapter pipeline around *function*.

    The adapter composes up to five stages into a single callable with
    signature ``(fn, *args, **kwargs) -> result``:

        1. on_call   (optional) — observe the call before it happens
                                  (e.g. writer.sync or writer.write_call)
        2. proxy_input          — transform each argument (starting from
                                  position 1) from one domain to the other
        3. function             — execute the call (or replay it)
        4. proxy_output         — transform the result back
        5. on_result / on_error — observe the outcome
                                  (e.g. writer.write_result / write_error)

    Parameters
    ----------
    function : callable
        The core operation.  For record this is typically
        ``gate.apply_with(None)`` (execute then clear the gate).
        For replay this is ``_run_with_replay(reader.read_result)``.
    proxy_input : callable
        Applied to each argument (except the first, which is the
        function itself) to translate values crossing the boundary.
    proxy_output : callable
        Applied to the return value to translate it back.
    on_call : callable, optional
        Observer invoked before the call (receives the same args).
    on_result : callable, optional
        Observer invoked on success (receives the result).
    on_error : callable, optional
        Observer invoked on exception (receives the error).
    """

    if on_call:
        function = utils.observer(on_call = on_call, function = function)

    function = functional.mapargs(starting = 1, transform = proxy_input, function = function)

    function = functional.sequence(function, proxy_output)

    if on_result or on_error:
        function = utils.observer(on_result = on_result, on_error = on_error, function = function)

    return function

def proxy(proxytype):
    """Create a callable that wraps a value in a proxy type.

    Given a value, looks up its type, passes it through *proxytype* to
    get the proxy class, then wraps it with ``utils.create_wrapped``.
    """
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, proxytype),
        None)

def maybe_proxy(proxytype):
    """Conditionally proxy a value.

    If the value is already a ``Wrapped`` instance, unwrap it (avoid
    double-wrapping).  Otherwise, create a proxy using *proxytype*
    (memoized per type so each source class gets one proxy class).
    """
    return functional.if_then_else(
            functional.isinstanceof(utils.Wrapped),
            utils.unwrap,
            proxy(functional.memoize_one_arg(proxytype)))

class Patched:
    """Marker base class for user-defined patched types.

    When a class inherits from Patched, the system recognises it as an
    explicitly patched type (rather than an automatically discovered one)
    and can apply custom proxy patches via ``__retrace_patch_proxy__``.
    """
    __slots__ = ()

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

    record_context(w)     Context manager.  Inside it, external calls are
                          executed and recorded via the writer ``w``.

    replay_context(r)     Context manager.  Inside it, external calls are
                          skipped — results are read from the reader ``r``.

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
        return self._external.bind(fn)

    def patch(self, obj):
        """Patch *obj* for proxying — dispatches by type.

        If *obj* is a class, delegates to ``patch_type`` (mutates the
        class in-place, returns ``None``).

        If *obj* is a callable (function, builtin, etc.), delegates to
        ``patch_function`` (returns a new ``BoundGate`` wrapper).

        Raises ``TypeError`` for anything else.
        """
        if isinstance(obj, type):
            self.patch_type(obj)
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
        """
        apply_ext = self._external.apply_with(None)
        apply_int = self._internal.apply_with(None)

        return functional.partial(apply_ext, functional.partial(apply_int, function))

        # def wrapper(*args, **kwargs):
        #     return apply_ext(apply_int, function, *args, **kwargs)

        # return wrapper

    def is_bound(self, obj):
        """Return True if *obj* is retraced (not in the _unretraced set).

        Only objects of patched types can be bound — the _on_alloc hook
        that populates _unretraced only fires for those types.  Existing
        instances are swept into _unretraced when patch_type(cls) runs.
        Objects whose type was never patched are always unbound.
        """
        return type(obj) in self.patched_types and obj not in self._unretraced

    def is_retraced(self, obj):
        """Return True if *obj* is bound or is a dynamic proxy wrapper."""
        return self.is_bound(obj) or utils.is_wrapped(obj)

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

    def __init__(self) -> None:
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
        self._internal = utils.Gate()
        self._external = utils.Gate()
        
        # ── Binding / allocation gates ────────────────────────────
        #
        # These are gates whose default state is noop (not None).
        # They are called from _on_alloc when a patched type is
        # instantiated, to notify the writer/reader that a new object
        # has entered the boundary.
        #
        # _new_patched: allocation-origin notification.  Receives the
        #               allocated object itself; backends can recover
        #               the concrete type from the live object.
        # _bind:        generic binding hook kept separate from
        #               allocation-origin semantics.
        self._new_patched = utils.Gate(default = utils.noop)
        self._bind = utils.Gate(default = utils.noop)

        # ── Unretraced set ────────────────────────────────────────
        #
        # Tracks instances of patched types that were created OUTSIDE
        # any record/replay context.  Since the vast majority of
        # instances are created inside a context (and thus retraced),
        # this set stays small.  is_bound(obj) is simply:
        #   obj not in _unretraced
        #
        # Uses MemoryAddresses (C-level raw-pointer set) — no id()
        # boxing, O(1) contains, and add() returns a remover callable
        # that integrates with the on_alloc dealloc hook for automatic
        # cleanup when the object is freed.
        self._unretraced = utils.MemoryAddresses()

        # ── Type tracking ──────────────────────────────────────────
        self.patched_types = set()        # types already patched in-place
        self.immutable_types = set()      # types that pass through as-is
        self.base_to_patched = {}         # base cls → user-defined Patched subclass
        self.replay_materialize = set()   # functions safe to call for real on replay

        # should_proxy(value) → bool: given a value, check its type
        # and decide whether it needs a dynamic proxy wrapper.
        # Memoized so each type is only checked once.
        self.should_proxy = functional.sequence(
            functional.typeof, 
            functional.memoize_one_arg(self._should_proxy_type))

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
        # base types (int→ext calls).  It checks _unretraced before
        # dispatching:
        #   1. positional_param(1) extracts the instance (args[1],
        #      since WrappedFunction prepends the target at args[0]).
        #   2. _unretraced.contains checks if the instance was
        #      created outside any record/replay context.
        #   3. If unretraced → functional.apply passes through to
        #      the original target (no interception).
        #      If retraced → route through _external gate.
        #
        # _int_handler is the raw _internal gate.  Subclass methods
        # (ext→int callbacks) are always on retraced instances, so
        # no _unretraced check is needed — the gate dispatches to
        # the executor when active, or passes through when disabled.
        self._ext_handler = functional.if_then_else(
            functional.sequence(functional.positional_param(1), self._unretraced.contains),
            functional.apply,
            self._external)

        # Internal overrides always route through the internal gate while
        # retrace is active. When retrace is disabled, ``self._internal``
        # naturally passthroughs to the original target.
        self._int_handler = self._internal

        # ── Allocation hook ────────────────────────────────────────
        #
        # _on_alloc is installed on every patched type via
        # utils.set_on_alloc.  It is called from tp_alloc whenever a
        # new instance of the patched type (or any of its subclasses)
        # is created.
        #
        # The allocation gate (_new_patched) is called directly —
        # gate(obj) dispatches to the executor when set, or to the
        # default (utils.noop) when no context is active.
        #
        # The logic:
        #   - When the external gate is active: _bind(obj), because the
        #     concrete object already exists and only needs a stream
        #     identity.
        #   - When the internal gate is active: _new_patched(obj)
        #     so replay can materialize patched objects by type.
        #   - Otherwise: _unretraced.add(obj) → mark as unbound so
        #     _ext_handler will passthrough calls on this instance.
        self._on_alloc = functional.cond(
            self._external.is_set, self._bind,
            self._internal.is_set, self._new_patched,
            self._unretraced.add)

    @property
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

    def _context(self, **kwargs):
        """Build a reusable, thread-safe context manager for gate executors.

        Each keyword argument names a private gate attribute on self
        (e.g. ``_internal``, ``_external``, ``_new_patched``, ``_bind``).
        The corresponding gate's executor is saved on ``__enter__``,
        replaced with the given value, then restored on ``__exit__``.

        The returned ``_GateContext`` can be entered from multiple
        threads concurrently — each thread's saved state is isolated
        via ``_thread._local()``.

        This is the primitive that record_context and replay_context
        are built on.
        """
        return _GateContext(self, **kwargs)

    def _should_proxy_type(self, cls):
        """Decide whether values of *cls* need a dynamic proxy wrapper.

        Returns False for:
          - ``object`` itself (everything is an object, skip it)
          - any subclass of a type in ``immutable_types`` (e.g. int,
            str, bytes — their values pass through the boundary as-is)
          - any type already in ``patched_types`` (already handled)
        """
        return cls is not object and \
                not issubclass(cls, tuple(self.immutable_types)) and \
                cls not in self.patched_types
                                
    def patch_type(self, cls):
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

        4. **Existing-instance sweep** — any already-live instances of
           the patched type family are added to ``_unretraced`` so they
           passthrough as live runtime state.

        5. **Bind notification** — ``_bind(cls)`` is called to notify
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

        existing = getattr(cls, '__retrace_system__', None)
        if existing is not None and existing is not self:
            raise RuntimeError(
                f"patch_type: {cls.__qualname__} is already patched by "
                f"another System instance")

        assert cls not in self.patched_types

        missing = object()
        alloc_patch_undo = None
        unretraced_undo = []
        patched_attrs = {}
        patched_subtypes = []
        patched_family = {cls}
        subtype_attrs = {}
        original_init_subclass = cls.__dict__.get('__init_subclass__', missing)
        original_retrace = cls.__dict__.get('__retrace__', missing)
        original_retrace_system = cls.__dict__.get('__retrace_system__', missing)

        def restore_attr(target, name, original):
            if original is missing:
                if name in target.__dict__:
                    delattr(target, name)
            else:
                setattr(target, name, original)

        def proxy_attrs(cls, dict, handler, originals):
            """Replace callables and descriptors in *dict* on *cls*.

            Iterates over *dict* (which may be cls.__dict__ or the
            merged superdict).  For each attribute not in the
            blacklist:
              - Member/GetSet descriptors → proxy_member
              - Callables → proxy_function
            """
            blacklist = self._patch_type_blacklist

            def proxy_function(func):
                return utils.wrapped_function(handler = handler, target = func)

            def proxy_member(member):
                return utils.wrapped_member(handler=handler, target=member)

            for name, value in dict.items():
                if name not in blacklist:
                    if name not in originals:
                        originals[name] = getattr(cls, name)
                    if type(value) in [types.MemberDescriptorType, types.GetSetDescriptorType]:
                        setattr(cls, name, proxy_member(value))
                    elif callable(value):
                        setattr(cls, name, proxy_function(value))

        def mark_existing_instances_unretraced(types_to_mark):
            for obj in gc.get_objects():
                if type(obj) in types_to_mark:
                    remove = self._unretraced.add(obj)
                    if remove is not None:
                        unretraced_undo.append(remove)
        try:
            with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):

                # Install the alloc hook first so later failures cannot leave
                # the type method-wrapped but missing allocation bookkeeping.
                alloc_patch_undo = utils.set_on_alloc(cls, self._on_alloc)
                self.patched_types.add(cls)

                # Step 1: Patch cls's methods as external
                base_methods = superdict(cls)

                proxy_attrs(cls, dict=base_methods, handler=self._ext_handler, originals=patched_attrs)

                cls.__retrace_system__ = self

                # Step 3: Patch subclasses as internal
                #
                # Only wrap methods that override a name defined on the
                # patched base type.  C extension code can only dispatch
                # to methods it knows about — names in its own MRO.  A
                # brand-new method on the subclass (not an override) can
                # never be reached from C code, so wrapping it is pure
                # overhead.
                if utils.is_extendable(cls):
                    base_method_names = frozenset(base_methods.keys())

                    def init_subclass(cls, **kwargs):
                        self.patched_types.add(cls)
                        patched_subtypes.append(cls)
                        patched_family.add(cls)

                        overrides = {
                            name: value
                            for name, value in cls.__dict__.items()
                            if name in base_method_names
                        }
                        originals = subtype_attrs.setdefault(cls, {})
                        proxy_attrs(cls, dict=overrides, handler=self._int_handler, originals=originals)

                    cls.__init_subclass__ = classmethod(init_subclass)

                    for subtype in get_all_subtypes(cls):
                        with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                            init_subclass(subtype)

                cls.__retrace__ = self

            mark_existing_instances_unretraced(patched_family)

            # Step 4: Notify the bind gate
            self._bind(cls)
        except Exception:
            for undo in reversed(unretraced_undo):
                undo()

            if alloc_patch_undo is not None:
                alloc_patch_undo()

            for subtype in reversed(patched_subtypes):
                originals = subtype_attrs.get(subtype, {})
                with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                    for name, original in reversed(list(originals.items())):
                        restore_attr(subtype, name, original)
                self.patched_types.discard(subtype)

            with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
                for name, original in reversed(list(patched_attrs.items())):
                    restore_attr(cls, name, original)
                restore_attr(cls, '__init_subclass__', original_init_subclass)
                restore_attr(cls, '__retrace_system__', original_retrace_system)
                restore_attr(cls, '__retrace__', original_retrace)

            self.patched_types.discard(cls)
            raise

        return cls

    def _proxyfactory(self, proxytype):
        """Build a value transformer that wraps objects crossing the boundary.

        Returns a ``functional.walker`` that recursively walks a value.
        For each element, if ``should_proxy`` says the value's type
        needs wrapping, it is passed through ``maybe_proxy(proxytype)``
        which either unwraps an already-wrapped value or creates a new
        proxy.

        The *proxytype* callable is run with both gates disabled (via
        ``disable_for``) to prevent re-entrancy during proxy class
        construction.
        """
        return functional.walker(functional.when(self.should_proxy, maybe_proxy(proxytype)))

    def _create_context(self, int_spec, ext_spec, ext_runner = None, **args):
        """Build the executor pair and enter a gate context.

        This is the core wiring that record_context and replay_context
        both delegate to.  It builds two executors and loads them into
        the gates via ``_context``.

        Parameters
        ----------
        int_spec : SimpleNamespace
            Specification for the internal (ext→int) side.  Fields:
            ``proxy`` (value transformer), ``on_call``, ``on_result``,
            ``on_error`` (observers).

        ext_spec : SimpleNamespace
            Specification for the external (int→ext) side.  Same fields.

        ext_runner : callable, optional
            If provided, external calls are replaced with
            ``ext_runner()`` (used for replay — reads from the stream
            instead of executing the real function).

        **args
            Additional gate executors to set (e.g. ``_new_patched``,
            ``_bind``).

        Executor construction
        ---------------------

        ext_executor (loaded onto _external gate):
            The full int→ext adapter pipeline:

                on_call(ext_spec) → proxy_input(int_spec) → function → proxy_output(ext_spec) → on_result(ext_spec)

            *function* is either:
              - ``self._external.apply_with(None)`` for record — execute
                the real function with the external gate temporarily
                cleared (prevents the adapter from re-entering itself
                on nested external calls).
              - ``_run_with_replay(ext_runner)`` for replay — ignore the
                real function and return the recorded result.

        int_executor (loaded onto _internal gate):
            Handles ext→int callbacks with a re-entrancy check:

              - ``self._external.test(None)`` checks if the external
                gate's executor is currently None.  This is True when
                we are already inside an external call (the ext_executor
                cleared it with apply_with(None)).  In that case, the
                callback is a nested internal call — just pass through
                with ``functional.apply``.

              - Otherwise (external gate still has an executor), this is
                a genuine ext→int callback.  Wrap it with the full
                adapter pipeline in the reverse direction:

                    on_call(int_spec) → proxy_input(ext_spec) → function → proxy_output(int_spec) → on_result(int_spec)

                *function* here is ``self._external.apply_with(ext_executor)``
                which temporarily restores the ext_executor on the
                external gate so that if the callback makes an outbound
                call, it goes through the full ext adapter again.

            Known issue — passthrough gap
            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

            The test(None) check conflates two cases:

              1. A nested internal call during an external call where
                 no boundary crossing occurs — passthrough is correct.

              2. C base class code calling a Python override during an
                 external call — a genuine ext→int callback.  The
                 passthrough means:
                   (a) The callback is not recorded (write_call skipped).
                   (b) The external gate stays None, so outbound calls
                       from within the override (e.g. super().recv())
                       bypass the external pipeline — also unrecorded.

            The adapter branch would be correct for case 2: it records
            the callback and restores the external gate via
            apply_with(ext_executor) so outbound calls are intercepted.
            Fixing this requires a way to distinguish case 1 from
            case 2 — e.g. tracking whether the caller is C code or
            Python code, or using a depth counter.
        """

        function = _run_with_replay(
            ext_runner,
            replay_materialize = self.replay_materialize,
            materialize = lambda fn, *args, **kwargs: self.disable_for(fn)(*args, **kwargs),
        ) if ext_runner \
            else self._external.apply_with(None)

        ext_executor = adapter(
            function = function,
            proxy_input = int_spec.proxy,
            proxy_output = ext_spec.proxy,
            on_call = ext_spec.on_call,
            on_result = ext_spec.on_result,
            on_error = ext_spec.on_error)

        int_executor = adapter(
            function = self._external.apply_with(ext_executor),
            proxy_input = ext_spec.proxy,
            proxy_output = int_spec.proxy,
            on_call = int_spec.on_call,
            on_result = int_spec.on_result,
            on_error = int_spec.on_error)

        return self._context(
            _internal = int_executor,
            _external = ext_executor,
            **args)

    def _create_int_spec(self, bind, on_call : Callable = None,
                         on_result : Callable = None,
                         on_error : Callable = None) -> SimpleNamespace:
        """Build the internal (ext→int) specification.

        Parameters
        ----------
        bind : callable
            Called when a new internal proxy object is created.  During
            record this is ``writer.bind``; during replay this is
            ``reader.bind``.
        on_call : callable or None
            Observer called when an ext→int callback fires.  During
            record this is ``writer.write_call``; during replay this
            is None (callbacks are re-invoked, not read from stream).
        on_result : callable or None
            Observer called when an ext→int callback returns.  Used by
            normalize to checkpoint the result for divergence detection.
        on_error : callable or None
            Observer called when an ext→int callback raises.  Used by
            normalize to checkpoint the error for divergence detection.

        Returns
        -------
        SimpleNamespace with fields:
            proxy     — value transformer for the internal domain
            on_call   — observer (or None)
            on_result — result observer (or None)
            on_error  — error observer (or None)
        """

        def int_proxytype(cls):
            return dynamic_int_proxytype(
                handler = self._internal,
                cls = cls,
                bind = bind)

        return SimpleNamespace(
            proxy = self._proxyfactory(self.disable_for(int_proxytype)),
            on_call = on_call,
            on_result = on_result,
            on_error = on_error)

    def _create_ext_spec(self, sync : Callable, on_result : Callable, on_error : Callable,
                         on_new_proxytype : Callable = None,
                         disabled_handler : Callable = functional.apply,
                         internal_handler : Callable = None) -> SimpleNamespace:
        """Build the external (int→ext) specification.

        Parameters
        ----------
        sync : callable
            Called before the external function executes (no arguments).
            During record this is ``writer.sync`` (flushes the stream).
            During replay this is ``reader.sync``.
        on_result : callable or None
            Observer called on success.  During record this is
            ``writer.write_result``; during replay this is None.
        on_error : callable or None
            Observer called on exception.  During record this is
            ``writer.write_error``; during replay this is None.
        on_new_proxytype : callable or None
            Called as ``on_new_proxytype(proxytype, cls)`` whenever
            ``ext_proxytype`` creates a new DynamicProxy class.  During
            record this registers a ``type_serializer`` on the writer
            so that DynamicProxy instances are stored as StubRefs.
            During replay this is None.

        Returns
        -------
        SimpleNamespace with fields:
            proxy     — value transformer for the external domain
            on_call   — sync observer
            on_result — result observer (or None)
            on_error  — error observer (or None)
        """

        if internal_handler is None:
            internal_handler = self._external

        handler = self.create_gate(
            disabled = disabled_handler,
            external = self._external,
            internal = internal_handler,
        )

        def ext_proxytype(cls):
            proxytype = dynamic_proxytype(handler = handler, cls = cls)
            proxytype.__retrace_source__ = 'external'

            if issubclass(cls, Patched):
                patched = cls
            elif cls in self.base_to_patched:
                patched = self.base_to_patched[cls]
            else:
                patched = None

            assert patched is None or patched.__base__ is not object

            if patched:
                patcher = getattr(patched, '__retrace_patch_proxy__', None)
                if patcher:
                    patcher(proxytype)

            if on_new_proxytype:
                on_new_proxytype(proxytype, cls)

            return proxytype

        return SimpleNamespace(
            proxy = self._proxyfactory(self.disable_for(ext_proxytype)),
            on_call = functional.lazy(sync),
            on_result = on_result,
            on_error = on_error)

    def record_context(self, writer, normalize = None, stacktraces = False):
        """Context manager for recording.

        Inside this context, all calls to methods on patched types go
        through the adapter pipeline.  External calls execute normally
        and their results are written to *writer*.  Callbacks from
        external code into internal code are also recorded.

        Parameters
        ----------
        writer : object
            Must provide: ``bind(obj)``, ``new_patched(obj)``,
            ``write_call(*a, **kw)``, ``sync()``,
            ``write_result(*a, **kw)``, ``write_error(*a, **kw)``.
            If *normalize* is set, must also provide
            ``checkpoint(value)``.
            If *stacktraces* is True, must also provide
            ``stacktrace()``.

        normalize : callable or None
            Optional function that reduces a value to a canonical form
            for divergence detection.  When set, every external result
            and internal callback result/error is normalized and
            written as a checkpoint via ``writer.checkpoint``.  During
            replay the same normalization runs and
            ``reader.checkpoint`` compares against the stored value.

        stacktraces : bool
            When True, ``writer.stacktrace()`` is called at each call
            boundary — before every external call and before every
            internal callback.  The writer owns the capture strategy
            (e.g. ``StackFactory.delta()``); the system just calls it.

        Usage
        -----
            with system.record_context(writer):
                s = socket.socket(...)
                s.connect(addr)
                data = s.recv(1024)

            # With stack traces:
            with system.record_context(writer, stacktraces=True):
                ...
        """
        checkpoint = functional.sequence(normalize, writer.checkpoint) \
            if normalize else None

        def write_internal_call(_fn, *args, **kwargs):
            writer.write_call(*args, **kwargs)

        if stacktraces:
            def write_stack_then(*args, **kwargs):
                writer.stacktrace()
            ext_on_call = utils.chain(write_stack_then, writer.sync)
            int_on_call = utils.chain(write_stack_then, write_internal_call)
        else:
            ext_on_call = writer.sync
            int_on_call = write_internal_call

        def register_type_serializer(proxytype, cls):
            stub_ref = StubRef(cls)
            writer.type_serializer[proxytype] = lambda value: stub_ref

        return self._create_context(
            _new_patched = writer.new_patched,
            _bind = writer.bind,
            int_spec = self._create_int_spec(
                bind = writer.bind,
                on_call = int_on_call,
                on_result = checkpoint,
                on_error = checkpoint),
            ext_spec = self._create_ext_spec(
                sync = ext_on_call,
                on_result = utils.chain(writer.write_result, checkpoint),
                on_error = utils.chain(writer.write_error, checkpoint),
                on_new_proxytype = register_type_serializer))

    def replay_context(self, reader, normalize = None):
        """Context manager for replay.

        Inside this context, external calls are never executed.
        Instead, results are read from *reader* and returned directly.
        The customer's code runs identically to the recording because
        from its perspective the outside world produces the same values.

        Parameters
        ----------
        reader : object
            Must provide: ``bind(obj)``, ``sync()``,
            ``read_result() -> value``.
            If *normalize* is set, must also provide
            ``checkpoint(value)``.
            May provide ``type_deserializer`` dict for custom
            deserialization (e.g. StubRef → stub instance).

        normalize : callable or None
            Optional function that reduces a value to a canonical form
            for divergence detection.  When set, every external result
            and internal callback result/error is normalized and
            compared against the checkpoint stored during recording
            via ``reader.checkpoint``.  A mismatch indicates that
            replay has diverged from the original execution.

        Usage
        -----
            with system.replay_context(reader):
                s = socket.socket(...)      # returns recorded value
                s.connect(addr)             # returns recorded value
                data = s.recv(1024)         # returns recorded data
        """
        checkpoint = functional.sequence(normalize, reader.checkpoint) \
            if normalize else None

        if hasattr(reader, 'type_deserializer'):
            reader.type_deserializer[StubRef] = _ReplayStubFactory()

        native_reader = getattr(reader, '_native_reader', reader)
        if hasattr(native_reader, 'stub_factory'):
            # Replay-side stub materialization must not re-enter the active
            # gates. Some C types (for example _io.BufferedReader) allocate
            # patched objects during __new__, which would otherwise recurse
            # back into bind/new_patched mid-materialization.
            native_reader.stub_factory = self.disable_for(native_reader.stub_factory)

        return self._create_context(
            _bind = reader.bind,
            int_spec = self._create_int_spec(
                bind = reader.bind,
                on_result = checkpoint,
                on_error = checkpoint),
            ext_spec = self._create_ext_spec(
                sync = reader.sync,
                on_result = checkpoint,
                on_error = checkpoint,
                disabled_handler = functional.mapargs(
                    starting = 1,
                    transform = utils.try_unwrap,
                    function = functional.apply,
                ),
                internal_handler = functional.mapargs(
                    starting = 1,
                    transform = utils.try_unwrap,
                    function = functional.apply,
                )),
            ext_runner = reader.read_result)