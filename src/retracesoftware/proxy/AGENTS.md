# Proxy Layer

This directory implements the record/replay boundary. Code here decides what
crosses between deterministic "internal" code and nondeterministic "external"
code, what gets written to the trace, what gets replayed from the trace, and
how divergence is detected. Small changes here can silently corrupt replay.

## Hard Rules (Non-Negotiable)

1. **Replay never calls the live external callable, when retrace is enabled.** Replay reads the recorded
   `RESULT` or `ERROR` from the message stream and returns or raises the
   recorded outcome. The only exception is `system.replay_materialize`, a
   narrow opt-in escape hatch documented in `DESIGN.md` -> "Materialized
   Replay". If a fix appears to require running real external code during
   replay, the diagnosis is wrong.
2. **Materialized objects are reconstructed via binding + stub flow**, not by
   re-executing the original constructor. See `DESIGN.md` -> "Materialization
   flow" and "Materialized Replay" before changing any materialization code.
3. **Control-plane work must bypass the gates** via `disable_for()`. Recording
   debugger, monitoring, or replay plumbing into the trace causes silent
   desynchronization.
4. **Message order is contract.** Do not introduce, drop, reorder, or coalesce
   `CALL`, `CALLBACK`, `RESULT`, `ERROR`, `CHECKPOINT`, bind open/close, or
   `THREAD_SWITCH` events. Replay alignment is a hard invariant, not a
   convention.
5. **Prefer the narrowest fix in the responsible handler.** The CLI
   runtime path is `__main__.py` -> `proxy/tape.py` (Protocol types) +
   `proxy/io.py` -> `proxy/system.py` -> `proxy/gateway.py`
   (which delegates `patch_type` / `unpatch_type` to `proxy/patchtype.py`,
   uses `proxy/proxytype.py` reaching `proxy/stubfactory.py`, uses
   `proxy/typeutils.py` via `patchtype.py`, and uses `install/`). If a
   single diff touches more than one of `system.py`, `gateway.py`,
   `patchtype.py`, `io.py`, or `proxytype.py`, stop and re-read
   `DESIGN.md` before continuing.
6. **Do not add backwards-compatibility shims** for old trace formats,
   message tags, or removed APIs. If a recording no longer matches the
   current code, regenerate the recording. Do not pattern new code after
   anything you find under `proxy/` that is not listed in this file.
7. **Prioritize simplicity above all else.** When two correct designs
   exist, pick the smaller one. Do not add abstractions, indirection, or
   "extensibility hooks" without a concrete consumer that needs them.
   Deletion is preferred over generalization. The proxy kernel is
   already large enough.
8. **Tracing is observational; it must never alter thread scheduling.**
   See `DESIGN.md` -> "Threads" -> "Cross-thread synchronization".
   Smallest reproducer:
   `tests/install/stdlib/test_threading_lock_replay_regression.py`.
   If it fails, the portal/web replay regressions in the Proxy Kernel
   Sentinel Bundle are likely downstream.

## Read Order

Before editing proxy-layer code, read these in order:

1. `src/retracesoftware/proxy/AGENTS.md`
2. `src/retracesoftware/proxy/DESIGN.md`
3. The current CLI runtime path for the behavior in question. Verified
   by import graph:
   - `__main__.py` imports `proxy.tape` + `proxy.io` and the recording-I/O
     implementation from top-level `tape.py`.
   - `proxy/io.py` imports `proxy.system` plus `ext_replay_gateway` /
     `int_replay_gateway` from `proxy.gateway`.
   - `proxy/system.py` imports `proxy.proxytype`, `proxy.patchtype`,
     `install.patcher`, `install.edgecases`, and `ext_gateway` /
     `int_gateway` from `proxy.gateway`.
   - `proxy/gateway.py` only depends on `retracesoftware.utils` and
     `retracesoftware.functional`.
   - `proxy/patchtype.py` imports `proxy.proxytype` and `proxy.typeutils`.
   - `__main__.py` also reaches `system.is_bound.add(...)` directly (a
     `WeakSet` exposed on `System`) to mark recorder/tape plumbing as
     internal **without** firing `on_bind` — see `system.py` description
     below for why this matters.

   Read in this order:
   - `src/retracesoftware/__main__.py`
   - `src/retracesoftware/tape.py` (top-level, recording I/O implementation)
   - `src/retracesoftware/proxy/tape.py` (`Tape` / `TapeReader` /
     `TapeWriter` Protocol types only)
   - `src/retracesoftware/proxy/io.py`
   - `src/retracesoftware/proxy/system.py`
   - `src/retracesoftware/proxy/gateway.py` (record + replay gateway
     factories)
   - `src/retracesoftware/proxy/patchtype.py` (`patch_type` /
     `unpatch_type` and the in-place type-patching machinery)
   - `src/retracesoftware/proxy/proxytype.py`
   - `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`)
   - `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

Treat `DESIGN.md` as the behavior contract for the proxy kernel. When
debugging, first explain what the code is supposed to do according to
`DESIGN.md`, then identify where the current code or message flow deviates.
Do not start by "trying a fix" in `system.py`.

## Current Core Files

Verified against the actual import graph. Only files listed here are
considered live for AI guidance purposes; if a file under `proxy/` is
not listed below, do not pattern new code after it and do not assume
it influences runtime behavior.

### Live CLI runtime kernel (record + replay both go through these)

- `system.py`
  Gate-based kernel. Owns the `System` class — the phase thread-local
  `gate`, the `int_gateway` / `ext_gateway` callables built from that
  thread-local, the `int_gateway_factory` / `ext_gateway_factory`
  attributes (set to `gateway.int_gateway` / `gateway.ext_gateway` by
  default in `__init__`, swapped to the replay variants by
  `io.replayer()`), and the runtime methods (`__init__`, `run`,
  `disable_for`, `wrap_start_new_thread`, `wrap_async`, `patch_function`,
  `patch`, `int_proxytype`, `ext_proxytype`, `install`, plus the
  `location` property and the `_on_alloc` allocation hook).
  `System.patch()` dispatches to `patchtype.patch_type()` for class
  inputs; `System.unpatch_type()` delegates to
  `patchtype._module_unpatch_type()`. Module-level helpers include
  `LifecycleHooks`, `CallHooks`, `ProxyRef`, `ThreadSafeCounter`,
  `_ext_proxytype_from_spec`, `_NonBindingCallable`, and `fallback`.
  Imported by `proxy/io.py`, by tests, and by `install/`.

  Code-level facts not in `DESIGN.md` (do not regress):
  - `system.is_bound` (public `utils.WeakSet`) is the
    `System.is_retraced` predicate. `system.bind(obj)` =
    `is_bound.add(obj)` **plus** firing `on_bind` (which during
    recording writes a `NewBinding`). `__main__._bind_record_runtime`
    deliberately uses `system.is_bound.add(obj)` for tape/recorder
    plumbing that must never appear on the wire — confusing the two
    is a determinism bug.
  - `wrap_start_new_thread()` runs the child under
    `gate.apply_with('internal', wrapped)` so it starts in the
    internal phase with its own logical id (see `DESIGN.md` ->
    "Thread propagation"). `disable_for(fn)` stamps
    `__retrace_disabled_thread_target__` so the wrapper can skip
    `Thread.start()`'s `_started.wait()` and similar bootstrap. Do
    not strip the stamp.
  - `patch_function()` returns a `_NonBindingCallable` so module-level
    wrapped functions don't bind as methods when accessed through a
    class.
- `gateway.py`
  Pure factories that build the boundary pipelines. Module-level
  functions `ext_gateway`, `int_gateway`, `ext_replay_gateway`,
  `int_replay_gateway`, plus `ext_runner`, `int_runner` and the
  `unproxy_ext` / `unproxy_int` walkers. Each gateway factory takes
  `(gate, int_proxy, ext_proxy, hooks)` and returns the configured
  callable that `System.run()` installs into the matching
  `int_gateway` / `ext_gateway` thread-local. Has zero proxy-internal
  dependencies (only `retracesoftware.utils` and
  `retracesoftware.functional`); `system.py` imports the record-time
  factories at line 27 and `io.py` imports the replay-time factories
  at line 29. This is the file to read when reasoning about how a
  boundary crossing actually transforms arguments, observes hooks,
  and switches phase.
- `patchtype.py`
  Owns the in-place type-patching machinery: `patch_type`,
  `unpatch_type` (also exported as `_module_unpatch_type`),
  `get_all_subtypes`, `_unpatch_type_one`, `_unwrap_patched_attr`,
  `_restore_attr`, `_is_patch_generated_init_subclass`. Imports
  `superdict` from `proxy/proxytype.py` and `WithoutFlags` from
  `proxy/typeutils.py`. Called by `System.patch()`,
  `System.unpatch_type()`, and install/test code. Bugs in patching,
  subclass interception, alloc-hook installation, or `__init_subclass__`
  rewrites belong here, not in `system.py`.
- `io.py`
  `recorder()` / `replayer()` builders used by
  `src/retracesoftware/__main__.py`. Hosts `_RawTapeSource`,
  `_ReplayBindingState`, `_ThreadDemuxSource`, `_IoMessageSource`,
  `on_materialized_result`, `on_materialized_error`, and the
  `system.replay_materialize` registry initialization. `replayer()`
  swaps `system.ext_gateway_factory` to
  `functional.partial(ext_replay_gateway, ext_execute)` and
  `system.int_gateway_factory` to `int_replay_gateway` so that
  `System.run()` installs the replay-time pipelines. Within the proxy
  package imports `proxy.system` plus `ext_replay_gateway` /
  `int_replay_gateway` from `proxy.gateway` (line 31).

  Three replay-significant contracts live here. Read `DESIGN.md`
  ("Thread message ordering", "Materialized Replay" + "raw integer
  handle" paragraph) for the *why*; the symbol names below are where
  the *what* actually lives:
  - per-thread lookahead: `_ReplayBindingState._buffers`,
    `_ThreadDemuxSource.peek_buffered`
  - structural `equal()` with markers:
    `_unbound_external_marker`, `_checkpoint_external_marker`,
    `_equal_call_payload`, `_socketpair_args_with_defaults`
  - replay-time live materialization escape hatch:
    `live_materialized`, `live_file_descriptors`,
    `is_replay_materialize`, `next_materialized_result`, driven by
    `system.replay_materialize` (registered via
    `[module] replay_materialize = [...]` in
    `src/retracesoftware/modules/*.toml`, e.g. `posix.pipe`,
    `_thread.allocate_lock`, `threading.Lock`)
- `tape.py` (proxy)
  ≈40 lines. Defines the `Tape`, `TapeReader`, `TapeWriter` `Protocol`
  classes only. The recording I/O implementation lives in top-level
  `src/retracesoftware/tape.py`, which imports `TapeWriter` from here.
- `proxytype.py`
  Defines `DynamicProxy`, `superdict`, `method_names`,
  `dynamic_proxytype`, `dynamic_int_proxytype`. Live dependency of
  `system.py` and `patchtype.py`. On the runtime path, but rarely the
  right place to fix a record/replay bug — see "Where Bugs Usually
  Are NOT".
- `typeutils.py`
  `WithoutFlags`, `modify`. Used by `proxy/patchtype.py` (via
  `WithoutFlags` to temporarily clear `Py_TPFLAGS_IMMUTABLETYPE` while
  patching) and by `install/patcher.py`. Not imported directly by
  `system.py`, `gateway.py`, or `io.py`.
- `stubfactory.py`
  `Stub` plus stub generation for replay-time materialization. Reached
  via `proxytype.py` only; not imported directly from `system.py`,
  `gateway.py`, or `io.py`.

## Current Path

- **CLI runtime (record + replay both):**
  `src/retracesoftware/__main__.py` imports `recorder`, `replayer` from
  `proxy.io` and the `TapeReader` Protocol from `proxy.tape`. It also
  imports the recording-I/O implementation (`create_tape_writer`,
  `open_tape_reader`, `RawTapeWriter`) from top-level
  `src/retracesoftware/tape.py`.
  - `io.recorder()` builds a `System` whose `*_gateway_factory`
    attributes default to the record-time `ext_gateway` /
    `int_gateway` factories from `proxy/gateway.py`, and whose hooks
    write protocol events through `stream.Binder` and the recorder
    pipeline.
  - `io.replayer()` builds the same kernel but rebinds
    `system.ext_gateway_factory` to
    `functional.partial(ext_replay_gateway, ...)` and
    `system.int_gateway_factory` to `int_replay_gateway`, then attaches
    the replay source stack (`_RawTapeSource`, `_ThreadDemuxSource`,
    `_ReplayBindingState`, `_IoMessageSource`).
  - `System.run()` installs the configured factory output into the
    `int_gateway` / `ext_gateway` thread-local `on_then` slots and
    runs the user function under the `'internal'` phase so the first
    patched outbound call enters the external gateway.

  `system.py` delegates `patch_type` / `unpatch_type` to
  `proxy/patchtype.py`, uses `proxy/proxytype.py` (which reaches
  `proxy/stubfactory.py`), uses `proxy/typeutils.py` indirectly via
  `patchtype.py`, and uses `install/patcher.py` and
  `install/edgecases.py`. **That is the entire CLI proxy path.**
- `retracesoftware.protocol.replay` (the top-level `protocol/` package,
  not `proxy/`) handles in-memory / test readers and monitoring.
  Inspect it whenever a task touches monitoring or in-memory replay
  readers in addition to `io.py`.
- The active tests under `tests/proxy/` are: `test_io_memory_tape.py`,
  `test_monitoring.py`, `test_replay_materialize.py`, and
  `test_system_io_tape.py`. Plus `tests/test_main_memory_tape.py` and
  `tests/install/test_hash_patching.py` for adjacent install/replay
  coverage.

## Mental Model

- Internal code should re-execute during replay.
- External nondeterministic behavior must be intercepted at the boundary.
- A single phase thread-local (`System.gate`, values `None` /
  `'internal'` / `'external'`) tells retrace which side is currently
  executing.
- `System.ext_gateway` is active when phase is `'internal'` and routes
  internal -> external calls (patched base methods on patched types,
  patched standalone functions).
- `System.int_gateway` is active when phase is `'external'` and routes
  external -> internal callbacks (Python overrides on subclasses of
  patched base types).
- The two gateways alternate as control crosses the boundary; they are
  two directions of the same boundary, not independent systems.
- `patch_type()` (in `patchtype.py`) mutates types in-place so their
  methods route through the gateways.
- `System.patch_function()` wraps standalone callables through the
  external gateway.
- `disable_for()` clears the phase so retrace's own control-plane work
  does not re-enter the boundary logic.
- Outside an active `System.run()`, both gateways fall back to
  transparent passthrough; inside `run()`, the user function executes
  under `'internal'` phase so the first patched outbound call enters
  the external gateway instead of falling through.

## Design-First Debugging Workflow

When a proxy-layer bug is reported, work in this order:

1. State which `DESIGN.md` contract should hold:
   gate selection, phase (`disabled` / `internal` / `external`), callback
   routing, binding/materialization, or message ordering.
2. Identify the first observed mismatch:
   wrong gate, wrong phase, wrong message consumed, missing bind, unexpected
   passthrough, or materialization in the wrong context.
3. Trace the CLI runtime path before changing code:
   `__main__.py` -> `proxy/tape.py` (Protocol types) +
   top-level `src/retracesoftware/tape.py` (recording I/O) +
   `proxy/io.py` -> `proxy/system.py` -> `proxy/gateway.py`
   (with `proxy/patchtype.py`, `proxytype.py`, and `install/`). For
   "what does the boundary actually do to arguments and hooks?",
   `gateway.py` is the source of truth. For "how do types get
   patched?", `patchtype.py` is. For "how is the kernel wired and
   how does `run()` install gateways?", `system.py` is.
4. Prefer the narrowest fix in the responsible layer. Do not rewrite proxy
   kernel logic when the issue actually belongs in module patching, install
   config, or message plumbing.

If you cannot explain why the current behavior violates `DESIGN.md`, stop and
inspect the call flow again before editing code.

## Required Pre-Edit Statement

Before editing any file under `src/retracesoftware/proxy/`, state in chat:

1. The `DESIGN.md` rule that should hold (quote or paraphrase the relevant
   contract: gate, phase, callback routing, binding, materialization, or
   message order).
2. The first observed mismatch (file:line plus what gate, phase, or message is
   wrong).
3. The narrowest fix layer and why the fix does NOT belong one layer further
   out (module config in `src/retracesoftware/modules/*.toml`, install patcher,
   proxy handler, or proxy kernel).
4. Which sentinel tests from `tests/AGENTS.md` will be re-run.

If steps 1-3 cannot be completed from `DESIGN.md` and the live runtime path,
re-read `DESIGN.md` and trace the call flow again before editing. Do not
"try a fix" in `system.py`.

## Record And Replay

- `io.recorder()` builds the current record-time `System` wiring used
  by the CLI/runtime path. The kernel uses `gateway.ext_gateway` /
  `gateway.int_gateway` (the record-time factories from `gateway.py`),
  which execute the real external call inside `'external'` phase and
  emit `CALL` / `RESULT` / `ERROR` hooks.
- `io.replayer()` builds the current replay-time `System` wiring. It
  swaps `system.ext_gateway_factory` to
  `functional.partial(ext_replay_gateway, ...)` and
  `system.int_gateway_factory` to `int_replay_gateway`, so the same
  patched call sites enter `gateway.ext_replay_gateway` /
  `gateway.int_replay_gateway` instead. Replay does not execute the
  real external body; it reads the next recorded `RESULT` / `ERROR`
  via the supplied replay runner and returns or raises that.
- `normalize` + `checkpoint` are a divergence guard rail. They help
  locate the first mismatch but are not the entire correctness
  mechanism.
- In the current `io.py` path, stacktrace, checkpoint, thread-switch, and
  binding events are replay-significant, not incidental logging.
- Weakref callback markers and other older replay tags may still appear in
  compatibility surfaces, but they are not the default mental model for new
  proxy fixes.
- Replay-side stub/materialization paths must not accidentally re-enter the
  active gates while reconstructing proxyable objects.
- File-backed replay and in-memory replay share the same semantics but use
  different plumbing. Keep both paths in mind when changing thread or message logic.
- The current `io.py` tape path is driven by helper stages like
  `_RawTapeSource`, `_ReplayBindingState`, `_ThreadDemuxSource`, and
  `_IoMessageSource`.
- In the current `io.py` path, replay-visible allocation/materialization is
  often modeled through callback-like `create_stub_object` flow plus binding,
  not by treating older `ASYNC_NEW_PATCHED` compatibility messages as the main
  mental model.
- Proxyable external objects may be recorded as lightweight proxy metadata and recreated
  through replay-side materialization. Do not "simplify" this without tracing
  the object-identity consequences.
- Wrapped arguments seen inside external method bodies are part of the contract.
  Changes to wrapping/materialization that alter what method bodies observe can
  break replay even if the call signatures still look correct.
- Passthrough predicates for immutable or already-safe values are contract
  behavior, not just optimization. Changing passthrough semantics can break
  callback routing and materialization expectations for patched types.

## Message Stream Invariants

- The message stream ordering matters. In the current `io.py` path, be careful
  with `ON_START`, `THREAD_SWITCH`, `NEW_BINDING`, `BINDING_DELETE`, `CALL`,
  `CALLBACK`, `RESULT`, `ERROR`, `CALLBACK_RESULT`, `CALLBACK_ERROR`,
  `CHECKPOINT`, and `STACKTRACE`.
- Replay depends on message alignment. If replay consumes a different sequence
  of messages than record produced, everything after that point is wrong.
- Bind open/close handling is part of correctness, not bookkeeping noise.
  `_ReplayBindingState.consume_pending_closes()` and `_RawTapeSource` must stay
  aligned with what record writes.
- Thread-aware recordings depend on correct thread-switch handling and per-thread
  delivery. Be careful with any change that affects thread ids, sync points, or
  callback ordering.
- `MONITOR` and compatibility `SYNC` handling still matter in
  `retracesoftware.protocol.replay` / memory-reader paths. If a task touches
  monitoring or compatibility replay readers, inspect that file in addition to
  `io.py`.

## High-Risk Hazards

- Set iteration or unstable dict-order assumptions in replay-sensitive paths.
- `id()`, `hash()`, memory addresses, or object identity used for ordering.
- Weakref callbacks, `__del__`, GC timing, or cleanup side effects that can run
  in a different order during replay.
- Thread creation, locks, synchronization, and thread-id handling.
- `fork()` behavior, pid switching, and parent/child trace handling.
- New nondeterministic library behavior that is not intercepted at the boundary.
- I/O or logging in replay/control-plane paths that does not bypass the gates.
- Changes to wrapping, unwrapping, walker logic, stub refs, or materialization
  without tests for nested structures and callback paths.
- Anything that changes when `CALL` messages are emitted or consumed.
- Callback exceptions must round-trip with the same semantic behavior. Changes
  that swallow, rewrap, or reroute callback exceptions can break replay parity.

## Proxy Kernel Blast Radius

Treat these files as proxy-kernel files, not ordinary local implementation
details. They are the entire CLI proxy surface; changes here can cascade
across record, replay, install patching, and downstream library replay:

- `system.py`
- `gateway.py`
- `patchtype.py`
- `io.py`
- `proxytype.py`
- `typeutils.py` (reached via `patchtype.py`)
- `tape.py` (proxy — Protocol types)
- top-level `src/retracesoftware/tape.py` (recording I/O implementation)

Changes here can fix a proxy-boundary bug while reopening failures in:

- child-thread replay
- callback binding
- `anyio.from_thread` portal replay
- Starlette/FastAPI `TestClient`
- WSGI/socket cleanup after requests

Be especially cautious with changes to:

- the gateway factories themselves: `ext_gateway`, `int_gateway`,
  `ext_replay_gateway`, `int_replay_gateway`, `ext_runner`,
  `int_runner` (`gateway.py`)
- passthrough predicates and the `unproxy_ext` / `unproxy_int` walkers
  (`gateway.py`)
- `System.ext_proxy` / `System.int_proxy` walkers and `System.passthrough`
- `System._on_alloc` and `async_new_patched`
- `System.run()` gateway installation and `thread_wrapper`
- `_RawTapeSource`, `_ReplayBindingState`, `_ThreadDemuxSource`,
  `_IoMessageSource` (`io.py`)
- `on_materialized_result` / `on_materialized_error` and the
  `system.replay_materialize` registry (`io.py`)
- `patch_type()` subclass interception, `__init_subclass__` rewriting,
  and alloc-hook installation (`patchtype.py`)
- callback binding activation/deactivation
- wrapped-argument visibility inside external method bodies

If one of those changes lands, do not consider the change validated until the
adjacent replay sentinels have been rerun from `tests/`.

## Where Bugs Usually Are NOT

These files exist and look authoritative, but they are rarely (or never)
the right place to fix a record/replay or boundary bug:

- `proxytype.py`
  Proxy _type construction_. Editing it has historically broken
  serialization while a one-line fix in the responsible gateway
  pipeline in `gateway.py` or `io.py` hook would have been correct.
  If a fix lands here, justify why type construction itself is wrong
  rather than the per-call wrapping or message flow.
- `stubfactory.py`
  Replay-time stub generation. Reached via `proxytype.py`. Bugs here
  manifest at materialization time, but the cause is almost always in
  `_on_alloc`, `async_new_patched`, or `on_materialized_result` /
  `on_materialized_error`, not in stub construction itself.
- Anything under `proxy/` not listed in "Live CLI runtime kernel"
  above. Several files (`_binding_checkpoint.py`, `_system_patching.py`,
  `_system_threading.py`, `globalref.py`, `protocol.py`,
  `proxyfactory.py`, `serializer.py`, `startthread.py`) still exist on
  disk but have zero importers in `src/` or `tests/`. Do not pattern
  new code after them and do not assume they influence runtime
  behavior; they are scheduled for cleanup.

When in doubt, fix the smallest layer that owns the contract being
violated:
module config (`src/retracesoftware/modules/*.toml`) -> install patcher ->
gateway pipeline / `io.py` hook -> kernel (`system.py`). Move outward
only when the inner layer cannot express the fix.

## Working Rules

- Before changing proxy code, restate the relevant `DESIGN.md` expectation:
  what gate should run, what should be recorded or replayed, and what must
  stay aligned.
- Verify real CLI call sites from `src/retracesoftware/__main__.py`,
  `src/retracesoftware/tape.py` (top-level recording I/O),
  `src/retracesoftware/proxy/tape.py` (Protocol types only),
  `src/retracesoftware/proxy/io.py`,
  `src/retracesoftware/proxy/system.py`,
  `src/retracesoftware/proxy/gateway.py`,
  `src/retracesoftware/proxy/patchtype.py`,
  `src/retracesoftware/proxy/proxytype.py`,
  `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`),
  and `src/retracesoftware/install/`.
- If a fix can live in `src/retracesoftware/modules/*.toml` or the
  install layer, prefer that over rewriting boundary logic.
- If you change `system.py`, `gateway.py`, `patchtype.py`, `io.py`,
  `proxytype.py`, `typeutils.py`, or either `tape.py`, explicitly call
  out the determinism impact and explain how the change preserves
  gate selection, binding semantics, and message alignment.
- If you change replay message parsing, binding close consumption,
  thread demux, or materialization flow, check
  `tests/proxy/test_system_io_tape.py`,
  `tests/proxy/test_replay_materialize.py`, and the relevant
  install-level replay regressions before considering the patch safe.
- If you change anything in `wrap_start_new_thread`, `disable_for`,
  `_ReplayBindingState`, `_ThreadDemuxSource`, `equal()` markers, or
  the replay-time live-materialization escape hatches, run
  `tests/install/stdlib/test_threading_lock_replay_regression.py`
  (smallest cross-thread sync reproducer),
  `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`,
  and `tests/test_record_replay.py::test_record_then_replay_asyncio_run_coroutine_threadsafe`.
- If you change monitoring or compatibility replay-reader behavior, also
  check `tests/proxy/test_monitoring.py` and
  `src/retracesoftware/protocol/replay.py`.
- If you change wrapped-argument behavior, passthrough rules, or stub
  materialization, re-check the contract against binding/materialization
  tests rather than only looking at the immediate call site.
- Control-plane work such as debugger I/O, trace reads, or monitoring-related
  plumbing must remain invisible to replay.
- If you touch exception replay or message consumption in `io.py`, re-check
  object-lifetime and weakref/finalizer behavior, not just the happy path.
- Do not add backwards-compatibility shims for old trace formats, message
  tags, or removed APIs. Regenerate recordings instead.
- Prefer simpler over cleverer. Do not introduce new abstractions,
  factories, or "extensibility hooks" without a current, concrete
  consumer that needs them. Deletion is preferred over generalization.
- Add or update focused tests in the narrowest responsible layer.
- When debugging a replay failure, find the first divergence or misalignment
  instead of patching later symptoms.
- For proxy-kernel changes, prefer proving the first broken invariant with
  a focused test before broadening the patch.

## References

Live CLI runtime path (verified by import graph):

- `src/retracesoftware/proxy/DESIGN.md`
- `src/retracesoftware/__main__.py`
- `src/retracesoftware/tape.py` (top-level — recording I/O implementation)
- `src/retracesoftware/proxy/tape.py` (Protocol types only)
- `src/retracesoftware/proxy/io.py`
- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/proxy/gateway.py`
- `src/retracesoftware/proxy/patchtype.py`
- `src/retracesoftware/proxy/proxytype.py`
- `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`)
- `src/retracesoftware/proxy/stubfactory.py` (reached via `proxytype.py`)
- `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

Top-level protocol package (live, distinct from anything under `proxy/`):

- `src/retracesoftware/protocol/replay.py`

Tests (active under `tests/proxy/`):

- `tests/proxy/test_io_memory_tape.py`
- `tests/proxy/test_monitoring.py`
- `tests/proxy/test_replay_materialize.py`
- `tests/proxy/test_system_io_tape.py`

Adjacent tests touching this layer:

- `tests/test_main_memory_tape.py`
- `tests/install/test_hash_patching.py`
- `tests/install/stdlib/test_threading_lock_replay_regression.py`
  (smallest cross-thread sync reproducer; added in `0bad2cc`)
- `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`
- `tests/test_record_replay.py::test_record_then_replay_asyncio_run_coroutine_threadsafe`

Docs:

- `docs/THREAD_REPLAY.md`
- `docs/DEBUGGING.md`
