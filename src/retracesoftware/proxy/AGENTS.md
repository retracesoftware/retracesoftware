# Proxy Layer

This directory implements the record/replay boundary. Code here decides what
crosses between deterministic "internal" code and nondeterministic "external"
code, what gets written to the trace, what gets replayed from the trace, and
how divergence is detected. Small changes here can silently corrupt replay.

## Hard Rules (Non-Negotiable)

1. **Replay never calls the live external callable.** Replay reads the recorded
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
5. **Prefer the narrowest fix in the responsible handler.** If a diff touches
   more than one of `system.py`, `io.py`, `_system_specs.py`,
   `_system_patching.py`, `proxytype.py`, or `proxyfactory.py` in a single
   change, stop and re-read `DESIGN.md` before continuing.

## Read Order

Before editing proxy-layer code, read these in order:

1. `src/retracesoftware/proxy/AGENTS.md`
2. `src/retracesoftware/proxy/DESIGN.md`
3. The current runtime path for the behavior in question, usually:
   `src/retracesoftware/__main__.py`,
   `src/retracesoftware/proxy/tape.py`,
   `src/retracesoftware/proxy/io.py`,
   `src/retracesoftware/proxy/contexts.py`,
   `src/retracesoftware/proxy/_system_specs.py`,
   `src/retracesoftware/proxy/_system_patching.py`,
   `src/retracesoftware/proxy/system.py`

Treat `DESIGN.md` as the behavior contract for the proxy kernel. When
debugging, first explain what the code is supposed to do according to
`DESIGN.md`, then identify where the current code or message flow deviates.
Do not start by "trying a fix" in `system.py`.

## Current Core Files

These are the files actually imported by the live CLI runtime path
(`__main__.py` -> `tape.py` + `io.py` -> `system.py`) and by the current
test suite. Verify against the import graph if in doubt.

### Kernel and gate machinery

- `system.py`
  Current gate-based kernel (`System` class, `_external` / `_internal` gates,
  `_ext_handler`, `_int_handler`, `_override_handler`, `ext_executor`,
  `int_executor`, `patch_type`, `patch_function`, `disable_for`, `run`,
  `location`). Prefer this mental model first.
- `_system_specs.py`
  Builds the current internal/external proxy specs and gate behavior consumed
  by `System`.
- `_system_patching.py`
  Type-patching helper used by `System.patch_type()`.
- `_system_threading.py`
  `wrap_start_new_thread` helper for propagating active retrace context into
  child threads. The `System.wrap_start_new_thread` method drives this.
- `_system_adapters.py`
  Active adapter / `_run_with_replay` helper used by `replay_materialize` and
  by `tests/proxy/test_system_adapter.py` and
  `tests/proxy/test_replay_materialize.py`.
- `_binding_checkpoint.py`
  Small live helper for the deterministic bind-sequence checkpoint
  (`binding_name`, `checkpoint_bind`).

### Record / replay wiring

- `io.py`
  Current `recorder()` / `replayer()` builders used by
  `src/retracesoftware/__main__.py`. Hosts `_RawTapeSource`,
  `_ReplayBindingState`, `_ThreadDemuxSource`, `_IoMessageSource`,
  `on_materialized_result`, `on_materialized_error`, and the
  `system.replay_materialize` registry initialization.
- `contexts.py`
  `record_context()` / `replay_context()` factories. Used by tests, by
  `proxy/mode/`, and indirectly by `io.recorder()` / `io.replayer()`.
- `tape.py`
  `Tape`, `TapeReader`, `TapeWriter`. Imported directly by
  `src/retracesoftware/__main__.py` and is part of the live runtime entry
  point, not just a test helper.
- `context.py`
  `Context`, `LifecycleHooks`, `CallHooks`. Lower-level context object used by
  `proxy/mode/base.py`. Distinct from `contexts.py` (plural) — do not confuse
  the two.
- `_system_context.py`
  `_GateContext`, `Handler`. Older context-surface helpers; primarily exercised
  by the now-skipped `tests/proxy/test_system_context.py`. Treat as legacy
  support unless verified otherwise.

### Proxy type construction

- `proxytype.py`
  Live dependency of `system.py`, `_system_specs.py`, `_system_patching.py`.
  Defines `dynamic_proxytype`, `dynamic_int_proxytype`, `DynamicProxy`,
  `method_names`, `superdict`. Current code, but rarely the right place to
  fix a record/replay bug — see "Where Bugs Usually Are NOT".
- `proxyfactory.py`
  Proxy factory machinery. Same caveat as `proxytype.py`.
- `stubfactory.py`
  Stub objects used by `proxytype.py` for replay-time materialization stubs.
- `typeutils.py`
  `WithoutFlags`, `modify` helpers used by `_system_patching.py` and the
  install layer.

### New / not yet wired

- `mode/` (`base.py`, `record.py`, `replay.py`, `__init__.py`)
  Defines `Mode`, `RecordMode`, `ReplayMode` classes that wrap
  `record_context()` / `replay_context()`. **No live runtime code currently
  imports `proxy.mode`.** Treat as in-progress refactor scaffolding. Do not
  rely on it as the call path, and do not assume it is wired into
  `__main__.py`.

### Compatibility / DAP-only / unused

- `gateway.py`
  Older gate adapter. Used by `dap/replay/gate.py` and by legacy `proxysystem.py`
  / `record_system.py`. Not on the CLI runtime path. Do not delete; DAP depends
  on it.
- `record_system.py`
  Older `RecordSystem`. Imported only by `dap/replay/gate.py`. Same caveat as
  `gateway.py`.
- `proxysystem.py`, `record.py`, `replay.py`, `serializer.py`, `globalref.py`,
  `thread.py`
  Legacy stack. Only imported by each other and by `proxysystem.py`. Not used
  by the current CLI runtime path or by current tests. Inspect the import
  graph before treating any of these as authoritative.
- `messagestream.py`
  374-byte stub. Only imported by legacy `proxy/replay.py`. Effectively dead
  for the current runtime; do not reason about replay message flow from this
  file.
- `protocol.py`
  Not imported anywhere in the codebase. Effectively unused; do not reason
  about current contracts from this file. Distinct from
  `src/retracesoftware/protocol/` (the top-level protocol package), which
  *is* current and houses `protocol/replay.py`.

## Current vs Compatibility Paths

- The current top-level runtime path is:
  `src/retracesoftware/__main__.py` (imports `recorder`, `replayer` from
  `proxy.io` and `TapeReader` from `proxy.tape`) -> `io.recorder()` /
  `io.replayer()` builds a `System` from `system.py` using `_system_specs.py`,
  `_system_patching.py`, `contexts.py`, and `_system_threading.py`.
  `proxy/mode/` is *not* on this path today.
- Files not on the live CLI runtime path:
  `record.py`, `replay.py`, `proxysystem.py`, `serializer.py`, `globalref.py`,
  `thread.py`, `messagestream.py`, `protocol.py` (the file in `proxy/`, not the
  top-level package). These are the legacy stack and should not be the default
  mental model for new fixes.
- `gateway.py` and `record_system.py` are also legacy from the CLI runtime's
  perspective, but **the DAP replay path still imports them**
  (`src/retracesoftware/dap/replay/gate.py`). Do not delete or "clean up" these
  without checking DAP impact.
- `retracesoftware.protocol.replay` (the top-level package, not `proxy/protocol.py`)
  is still important for compatibility, monitoring, and some in-memory/test
  readers, but it is not the first file to trust for the live CLI/runtime call
  flow.
- `tests/proxy/test_system_context.py` is `pytest.mark.skip`ped and should not
  be used as the primary source of current behavior. The active tests under
  `tests/proxy/` are: `test_patch.py`, `test_monitoring.py`,
  `test_system_io_tape.py`, `test_system_direct_context.py`,
  `test_system_unpatch.py`, `test_system_adapter.py`,
  `test_replay_materialize.py`, `test_io_memory_tape.py`.

## Mental Model

- Internal code should re-execute during replay.
- External nondeterministic behavior must be intercepted at the boundary.
- `_external` handles internal -> external calls on patched types/functions.
- `_internal` handles external -> internal callbacks, especially Python
  overrides reached from C/base types.
- `patch_type()` mutates types in-place so their methods route through the gates.
- `patch_function()` wraps standalone functions through the external gate.
- `disable_for()` exists so retrace can perform its own control-plane work
  without re-entering the boundary logic.
- There is a documented ext->int passthrough gap around Python overrides called
  from C/base code. Be very careful when changing callback routing logic in
  `system.py`.

## Design-First Debugging Workflow

When a proxy-layer bug is reported, work in this order:

1. State which `DESIGN.md` contract should hold:
   gate selection, phase (`disabled` / `internal` / `external`), callback
   routing, binding/materialization, or message ordering.
2. Identify the first observed mismatch:
   wrong gate, wrong phase, wrong message consumed, missing bind, unexpected
   passthrough, or materialization in the wrong context.
3. Trace the current runtime path through `__main__.py`, `tape.py`, `io.py`,
   `contexts.py`, `_system_specs.py`, `_system_patching.py`, and `system.py`
   before changing code. Note that `proxy/mode/` is not currently wired in.
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

- `io.recorder()` builds the current record-time `System` wiring used by the
  CLI/runtime path.
- `io.replayer()` builds the current replay-time `System` wiring used by the
  CLI/runtime path.
- `contexts.record_context()` executes the real external call and records the
  outcome.
- `contexts.replay_context()` does not execute the external call; it reads the
  recorded outcome and returns it directly.
- `normalize` + `checkpoint` are a divergence guard rail. They help locate the
  first mismatch but are not the entire correctness mechanism.
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
details:

- `system.py`
- `io.py`
- `contexts.py`
- `_system_specs.py`
- `_system_patching.py`

Changes here can fix a proxy-boundary bug while reopening failures in:

- child-thread replay
- callback binding
- `anyio.from_thread` portal replay
- Starlette/FastAPI `TestClient`
- WSGI/socket cleanup after requests

Be especially cautious with changes to:

- passthrough predicates
- `_ext_handler`
- `_int_handler`
- `_override_handler`
- `_ext_proxy`
- `_int_proxy`
- `ext_execute`
- `async_new_patched`
- `_RawTapeSource`
- `_ReplayBindingState`
- `_ThreadDemuxSource`
- `on_call` / `sync` behavior
- callback binding activation/deactivation
- wrapped-argument visibility inside external method bodies

If one of those changes lands, do not consider the change validated until the
adjacent replay sentinels have been rerun from `tests/`.

## Where Bugs Usually Are NOT

These files exist and look authoritative, but they are rarely the right place
to fix a record/replay or boundary bug:

- `proxytype.py`, `proxyfactory.py`
  Proxy *type construction*. Editing these has historically broken
  serialization while a one-line fix in the responsible handler in `system.py`
  or `io.py` would have been correct. If a fix lands here, justify why type
  construction itself is wrong rather than the per-call wrapping or message
  flow.
- `record.py`, `replay.py`, `gateway.py`, `proxysystem.py`,
  `record_system.py`, `messagestream.py`
  Compatibility / older helper modules. Not the live CLI/runtime path.
  See "Current vs Compatibility Paths" above.
- `tests/proxy/test_system_context.py`
  Marked stale/skipped. Not a source of truth for current behavior.

When in doubt, fix the smallest layer that owns the contract being violated:
module config (`src/retracesoftware/modules/*.toml`) -> install patcher ->
proxy handler -> proxy kernel. Move outward only when the inner layer cannot
express the fix.

## Working Rules

- Before changing proxy code, restate the relevant `DESIGN.md` expectation:
  what gate should run, what should be recorded or replayed, and what must stay
  aligned.
- Prefer the current `System` path and verify real call sites from
  `src/retracesoftware/__main__.py`, `src/retracesoftware/proxy/tape.py`,
  `src/retracesoftware/proxy/io.py`,
  `src/retracesoftware/proxy/contexts.py`, and `src/retracesoftware/install/`.
- If a fix can live in `src/retracesoftware/modules/*.toml` or the install
  layer, prefer that over rewriting boundary logic.
- If you change `system.py`, `_system_threading.py`, or `_system_adapters.py`,
  explicitly call out the determinism impact.
- If you change `io.py`, `contexts.py`, `tape.py`, `_system_specs.py`, or
  `_system_patching.py`, explain how the change preserves gate selection,
  binding semantics, and message alignment.
- If you change replay message parsing, binding close consumption, thread demux,
  or materialization flow, check `tests/proxy/test_system_io_tape.py`,
  `tests/proxy/test_replay_materialize.py`, and the relevant install-level
  replay regressions before considering the patch safe.
- If you change monitoring or compatibility replay-reader behavior, also check
  `tests/proxy/test_monitoring.py` and `src/retracesoftware/protocol/replay.py`.
- If you change wrapped-argument behavior, passthrough rules, or stub
  materialization, re-check the contract against binding/materialization tests
  rather than only looking at the immediate call site.
- Control-plane work such as debugger I/O, trace reads, or monitoring-related
  plumbing must remain invisible to replay.
- If you touch exception replay or message consumption in `io.py` or
  `_system_adapters.py`, re-check object-lifetime and weakref/finalizer
  behavior, not just the happy path.
- Add or update focused tests in the narrowest responsible layer.
- When debugging a replay failure, find the first divergence or misalignment
  instead of patching later symptoms.
- For proxy-kernel changes, prefer proving the first broken invariant with a
  focused test before broadening the patch.

## References

Live runtime path:

- `src/retracesoftware/proxy/DESIGN.md`
- `src/retracesoftware/__main__.py`
- `src/retracesoftware/proxy/tape.py`
- `src/retracesoftware/proxy/io.py`
- `src/retracesoftware/proxy/contexts.py`
- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/proxy/_system_specs.py`
- `src/retracesoftware/proxy/_system_patching.py`
- `src/retracesoftware/proxy/_system_threading.py`
- `src/retracesoftware/proxy/_system_adapters.py`
- `src/retracesoftware/proxy/_binding_checkpoint.py`
- `src/retracesoftware/proxy/context.py`

Type construction (live but rarely the right fix site):

- `src/retracesoftware/proxy/proxytype.py`
- `src/retracesoftware/proxy/proxyfactory.py`
- `src/retracesoftware/proxy/stubfactory.py`
- `src/retracesoftware/proxy/typeutils.py`

In-progress (not wired into the CLI runtime):

- `src/retracesoftware/proxy/mode/`

DAP-only legacy (do not delete; DAP still imports them):

- `src/retracesoftware/proxy/gateway.py`
- `src/retracesoftware/proxy/record_system.py`

Compatibility / unused (verify before trusting):

- `src/retracesoftware/proxy/proxysystem.py`
- `src/retracesoftware/proxy/record.py`
- `src/retracesoftware/proxy/replay.py`
- `src/retracesoftware/proxy/serializer.py`
- `src/retracesoftware/proxy/globalref.py`
- `src/retracesoftware/proxy/thread.py`
- `src/retracesoftware/proxy/messagestream.py`
- `src/retracesoftware/proxy/protocol.py`
- `src/retracesoftware/proxy/_system_context.py`

Top-level protocol package (distinct from `proxy/protocol.py`):

- `src/retracesoftware/protocol/replay.py`

Tests (active):

- `tests/proxy/test_patch.py`
- `tests/proxy/test_monitoring.py`
- `tests/proxy/test_system_io_tape.py`
- `tests/proxy/test_system_direct_context.py`
- `tests/proxy/test_system_unpatch.py`
- `tests/proxy/test_system_adapter.py`
- `tests/proxy/test_replay_materialize.py`
- `tests/proxy/test_io_memory_tape.py`

Tests (stale / skipped):

- `tests/proxy/test_system_context.py` (`pytest.mark.skip`)

Docs:

- `docs/THREAD_REPLAY.md`
- `docs/DEBUGGING.md`
