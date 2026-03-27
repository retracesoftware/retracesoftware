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

import retracesoftware.utils as utils
import retracesoftware.functional as functional
from types import SimpleNamespace
from typing import Callable
from retracesoftware.proxy.protocol import ReaderProtocol, WriterProtocol
from ._system_adapters import (
    _run_with_replay,
    adapter,
    input_adapter,
    maybe_proxy,
    output_transform,
    proxy,
)
from ._system_context import _GateContext
from ._system_patching import Patched, patch_type as _patch_type_impl
from ._system_record import record_context as _record_context_impl
from ._system_replay import _ReplayStubFactory, replay_context as _replay_context_impl
from ._system_specs import (
    create_context as _create_context_impl,
    create_ext_spec as _create_ext_spec_impl,
    create_int_spec as _create_int_spec_impl,
)
from ._system_threading import (
    with_context,
    wrap_start_new_thread as _wrap_start_new_thread_impl,
)

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

    def wrap_start_new_thread(self, original_start_new_thread):
        """Wrap ``start_new_thread`` so child threads inherit active retrace context.

        The wrapper captures ``self.current_context`` from the parent thread
        at spawn time and, when retrace is active, rewrites the child target
        so it enters that same context before executing user code.
        """
        return _wrap_start_new_thread_impl(self, original_start_new_thread)

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
        
        self.current_context = utils.ThreadLocal(None)

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
        self._async_new_patched = utils.Gate(default = utils.noop)
        self._bind = utils.Gate(default = utils.noop)

        # ── Bound / unretraced sets ───────────────────────────────
        #
        # _bound tracks every object/type that was seen by bind/async_new_patched.
        # It uses a native hybrid weak set: weakrefable objects auto-evict,
        # others are held strongly for the lifetime of the System.
        self.is_bound = utils.WeakSet()

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

        # Return True if *obj* is bound or is a dynamic proxy wrapper.
        self.is_retraced = functional.or_predicate(self.is_bound, utils.is_wrapped)

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
        # base types (int→ext calls). It routes through the external gate
        # only for bound or wrapped instances; unbound patched instances
        # pass through to the original target.
        #
        # _int_handler is the raw _internal gate.  Subclass methods
        # (ext→int callbacks) dispatch to the executor when active, or
        # pass through when disabled.
        self._ext_handler = functional.if_then_else(
            functional.sequence(functional.positional_param(1), self.is_retraced),
            self._external,
            functional.apply)

        # Internal overrides always route through the internal gate while
        # retrace is active. When retrace is disabled, ``self._internal``
        # naturally passthroughs to the original target.
        self._int_handler = self._internal
        self._override_handler = self.create_dispatch(
            disabled=functional.apply,
            # Direct in-sandbox calls to Python overrides should behave like
            # normal Python method calls. Only true outside->inside callbacks
            # (which run while the external gate is temporarily cleared) should
            # route through the internal gate.
            external=functional.apply,
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
            self._external.is_set, utils.runall(self._bind, self.is_bound.add),
            self._internal.is_set, utils.runall(self._async_new_patched, self._bind, self.is_bound.add),
            utils.noop)

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
        (e.g. ``_internal``, ``_external``, ``_async_new_patched``, ``_bind``).
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

        return _patch_type_impl(self, cls)

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

    def _create_context(self, int_spec, ext_spec, ext_runner = None, replay_bind_materialized = None, **args):
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
            Additional gate executors to set (e.g. ``_async_new_patched``,
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
                   (a) The callback is not recorded (async_call skipped).
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

        return _create_context_impl(
            self,
            int_spec=int_spec,
            ext_spec=ext_spec,
            ext_runner=ext_runner,
            replay_bind_materialized=replay_bind_materialized,
            adapter_fn=adapter,
            replay_runner_fn=_run_with_replay,
            **args,
        )

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
            record this is ``writer.async_call``; during replay this
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

        return _create_int_spec_impl(
            self,
            bind=bind,
            on_call=on_call,
            on_result=on_result,
            on_error=on_error,
        )

    def _create_ext_spec(self, sync : Callable, 
                         on_result : Callable, 
                         on_error : Callable,
                         track : Callable,
                         on_passthrough_result : Callable = None,
                         on_new_proxytype : Callable = None,
                         disabled_handler : Callable = None,
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
        on_passthrough_result : callable or None
            Observer called when the adapter fast-path returns a value
            without crossing the proxy boundary.
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

        return _create_ext_spec_impl(
            self,
            sync=sync,
            on_result=on_result,
            on_error=on_error,
            track=track,
            on_passthrough_result=on_passthrough_result,
            on_new_proxytype=on_new_proxytype,
            disabled_handler=disabled_handler,
            internal_handler=internal_handler,
        )

    def record_context(self, writer: WriterProtocol, normalize = None, stacktraces = False):
        """Context manager for recording.

        Inside this context, all calls to methods on patched types go
        through the adapter pipeline.  External calls execute normally
        and their results are written to *writer*.  Callbacks from
        external code into internal code are also recorded.

        Parameters
        ----------
        writer : object
            Must provide: ``bind(obj)``, ``intern(obj)``, ``async_new_patched(obj)``,
            ``async_call(*a, **kw)``, ``sync()``,
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
        return _record_context_impl(
            self,
            writer,
            normalize=normalize,
            stacktraces=stacktraces,
        )

    def replay_context(self, reader: ReaderProtocol, normalize = None):
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
        return _replay_context_impl(
            self,
            reader,
            normalize=normalize,
        )
