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
   `proxy/io.py` -> `proxy/system.py` (which delegates `patch_type` /
   `unpatch_type` to `proxy/patchtype.py`, uses `proxy/proxytype.py`
   reaching `proxy/stubfactory.py`, uses `proxy/typeutils.py` via
   `patchtype.py`, and uses `install/`). If a single diff touches more
   than one of `system.py`, `patchtype.py`, `io.py`, `proxytype.py`, or
   `typeutils.py`, stop and re-read `DESIGN.md` before continuing. The
   `contexts.py` / `context.py` / `_system_specs.py` /
   `_system_patching.py` / `_system_adapters.py` cluster is
   **test/`mode/`-only** and is not on the CLI runtime path; editing it
   does not fix CLI record/replay bugs. `proxy/gateway.py` is
   **in-progress refactor scaffolding** (see "In-progress" below) — do
   not treat it as a live kernel file even though `system.py` references
   it.
6. **Do not add backwards-compatibility shims** for old trace formats,
   message tags, or removed APIs. If a recording no longer matches the
   current code, regenerate the recording. Do not pattern new code after
   anything you find under `proxy/` that is not listed in this file.
7. **Prioritize simplicity above all else.** When two correct designs
   exist, pick the smaller one. Do not add abstractions, indirection, or
   "extensibility hooks" without a concrete consumer that needs them.
   Deletion is preferred over generalization. The proxy kernel is
   already large enough.

## Read Order

Before editing proxy-layer code, read these in order:

1. `src/retracesoftware/proxy/AGENTS.md`
2. `src/retracesoftware/proxy/DESIGN.md`
3. The current CLI runtime path for the behavior in question. Verified
   by import graph (`__main__.py` imports `proxy.tape` + `proxy.io` and
   the recording-I/O implementation from top-level `tape.py`;
   `proxy/io.py` only imports from `proxy.system` within the proxy
   package; `proxy/system.py` imports `proxy.proxytype`, `proxy.patchtype`,
   and `install/` — and also tries to import `ext_gateway` /
   `int_gateway` from `proxy.gateway`, which is in-progress and currently
   does not resolve, see "In-progress" below; `proxy.patchtype` imports
   `proxy.proxytype` and `proxy.typeutils`):
   - `src/retracesoftware/__main__.py`
   - `src/retracesoftware/tape.py` (top-level, recording I/O implementation)
   - `src/retracesoftware/proxy/tape.py` (`Tape` / `TapeReader` /
     `TapeWriter` Protocol types only)
   - `src/retracesoftware/proxy/io.py`
   - `src/retracesoftware/proxy/system.py`
   - `src/retracesoftware/proxy/patchtype.py` (extracted from `system.py`
     in commit `d9181fb`; owns `patch_type` / `unpatch_type`)
   - `src/retracesoftware/proxy/proxytype.py`
   - `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`,
     not `system.py` directly)
   - `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

   The `contexts.py` / `context.py` / `_system_specs.py` /
   `_system_patching.py` / `_system_adapters.py` cluster is on the
   test/`mode/` path, not the CLI path — do not trace through it when
   debugging a CLI bug.

Treat `DESIGN.md` as the behavior contract for the proxy kernel. When
debugging, first explain what the code is supposed to do according to
`DESIGN.md`, then identify where the current code or message flow deviates.
Do not start by "trying a fix" in `system.py`.

## Current Core Files

Verified against the actual import graph. Only files listed here are
considered live for AI guidance purposes; if a file under `proxy/` is not
listed in this section or in "Test-only / `proxy/mode/`-only" below, do
not pattern new code after it and do not assume it influences runtime
behavior.

### Live CLI runtime kernel (record + replay both go through these)

- `system.py`
  Gate-based kernel. Owns the `System` class — the phase thread-local
  `gate`, the `int_gateway` / `ext_gateway` callables built from that
  thread-local, and the runtime methods (`__init__`, `run`,
  `disable_for`, `wrap_start_new_thread`, `wrap_async`, `patch_function`,
  `patch`, `int_proxytype`, `ext_proxytype`, `install`, plus the
  `location` property and the `_on_alloc` allocation hook).
  `System.patch_type()` / `System.unpatch_type()` are thin delegations
  to `proxy/patchtype.py`. Module-level helpers include `LifecycleHooks`,
  `CallHooks`, `ProxyRef`, `ThreadSafeCounter`, `_ext_proxytype_from_spec`,
  and `fallback`. Imported by `proxy/io.py`, by tests, and by `install/`.
  Also imports `ext_gateway` / `int_gateway` factory names from
  `proxy/gateway.py` (per `d9181fb`); those imports are part of an
  in-progress refactor and currently do not resolve — see "In-progress"
  below.
- `patchtype.py` (extracted from `system.py` in commit `d9181fb`)
  Owns the in-place type-patching machinery: `patch_type`, `unpatch_type`
  (also exported as `_module_unpatch_type`), `get_all_subtypes`,
  `_unpatch_type_one`, `_unwrap_patched_attr`, `_restore_attr`, and
  `_is_patch_generated_init_subclass`. Imports `superdict` from
  `proxy/proxytype.py` and `WithoutFlags` from `proxy/typeutils.py`,
  plus `install.edgecases.patchtype` indirectly through `system.py`.
  Called by `System.patch_type()` and `System.unpatch_type()`. Treat as
  a proxy-kernel file — bugs in patching, subclass interception, or
  alloc-hook installation belong here, not in `system.py`.
- `io.py`
  `recorder()` / `replayer()` builders used by
  `src/retracesoftware/__main__.py`. Hosts `_RawTapeSource`,
  `_ReplayBindingState`, `_ThreadDemuxSource`, `_IoMessageSource`,
  `on_materialized_result`, `on_materialized_error`, and the
  `system.replay_materialize` registry initialization. Within the proxy
  package it only imports from `proxy/system.py`.
- `tape.py` (proxy)
  ≈40 lines. Defines the `Tape`, `TapeReader`, `TapeWriter` `Protocol`
  classes only. The recording I/O implementation lives in top-level
  `src/retracesoftware/tape.py`, which imports `TapeWriter` from here.
- `proxytype.py`
  Defines `dynamic_proxytype`, `dynamic_int_proxytype`, `DynamicProxy`,
  `method_names`, `superdict`. Live dependency of `system.py` and
  `patchtype.py`. On the runtime path, but rarely the right place to fix
  a record/replay bug — see "Where Bugs Usually Are NOT".
- `typeutils.py`
  `WithoutFlags`, `modify`. Used by `proxy/patchtype.py` (via
  `WithoutFlags` to temporarily clear `Py_TPFLAGS_IMMUTABLETYPE` while
  patching) and by `install/patcher.py`. No longer imported directly by
  `system.py` after `d9181fb`.
- `stubfactory.py`
  Stubs used by `proxytype.py` for replay-time materialization. Reached
  via `proxytype.py`; not imported directly from `system.py` or `io.py`.

### Test-only / `proxy/mode/`-only (NOT on the CLI runtime path)

These files are imported by tests under `tests/proxy/` and `tests/install/`,
and by the experimental `proxy/mode/` package, but **not** by `proxy/io.py`,
`proxy/system.py`, or `__main__.py`. Editing them changes test/`mode/`
behavior, not CLI record/replay.

- `contexts.py`
  `record_context()` / `replay_context()` factories. Used by
  `tests/proxy/test_patch.py`, `test_system_unpatch.py`,
  `test_system_direct_context.py`, `test_system_adapter.py`,
  `tests/install/...`, and by `proxy/mode/record.py` + `proxy/mode/replay.py`.
- `context.py`
  `Context`, `LifecycleHooks`, `CallHooks`. Used by `proxy/mode/base.py`.
  Distinct from `contexts.py` (plural).
- `_system_specs.py`
  `create_context`, `create_int_spec`, `create_ext_spec`. Imported by
  `contexts.py` and `context.py`.
- `_system_patching.py`
  Defines `Patched`. Imported by `_system_specs.py`. (CLI `System.patch_type()`
  uses `install.patcher.install_hash_patching` and
  `install.edgecases.patchtype` instead.)
- `_system_adapters.py`
  `_run_with_replay`, `adapter`, `maybe_proxy`. Imported by
  `tests/proxy/test_system_adapter.py`,
  `tests/proxy/test_replay_materialize.py`, by `_system_specs.py`, and by
  `context.py`.

### In-progress (not wired into the CLI runtime today)

- `mode/` (`__init__.py`, `base.py`, `record.py`, `replay.py`)
  Defines `Mode`, `RecordMode`, `ReplayMode` wrapping
  `record_context()` / `replay_context()`. Intentional WIP — nothing in
  the live runtime imports `proxy.mode` yet. Treat as scaffolding for a
  future refactor; do not assume it is on the CLI path.
- `gateway.py` (gateway-split refactor scaffolding)
  `proxy/DESIGN.md` (rewritten in commit `d9181fb`) describes a target
  architecture where `gateway.py` exports `ext_gateway()` /
  `int_gateway()` / `ext_replay_gateway()` / `int_replay_gateway()`
  factories that `system.py` calls inside `System.run()` to install the
  record-time and replay-time pipelines. **The current `gateway.py`
  does NOT define those factories.** It still contains the older
  `Gates` class, `adapter_pair`, `create_context`, `create_int_spec`,
  `create_ext_spec`, `record_context`, `GatewayPair`, and `Recorder`
  symbols (verifiable via `grep -nE "^def |^class " gateway.py`). It
  also still does
  `from retracesoftware.proxy.system import _run_with_replay, adapter, Patched`,
  but those names were removed from `system.py` in `d9181fb` and do not
  exist anywhere in the current `system.py` either.

  Net effect today:
  - `proxy/system.py:27`
    (`from retracesoftware.proxy.gateway import ext_gateway, int_gateway`)
    is a forward reference into the in-progress refactor and will fail
    with `ImportError` until the factories land in `gateway.py`.
  - The kernel cannot be imported standalone via this commit.
  - `proxy/DESIGN.md` describes the **target** shape, not the current
    runtime behavior. Use it for the intended architecture, but do not
    treat current `gateway.py` content as authoritative.

  Until the refactor finishes:
  - Do not pattern new code after current `gateway.py` content (the
    older `Gates` / `adapter_pair` / `create_context` / `Recorder`
    surface is what `DESIGN.md` is replacing).
  - Do not "fix" the broken cross-imports by re-adding `_run_with_replay`,
    `adapter`, or `Patched` to `system.py` without explicit coordination
    — that contradicts the `d9181fb` direction.
  - When debugging a current CLI bug, treat `gateway.py` as not on the
    runtime path and look at `system.py`, `patchtype.py`, `io.py`,
    `proxytype.py`, and `install/` instead.

## Current Path

- **CLI runtime (record + replay both):**
  `src/retracesoftware/__main__.py` imports `recorder`, `replayer` from
  `proxy.io` and the `TapeReader` Protocol from `proxy.tape`. It also
  imports the recording-I/O implementation (`create_tape_writer`,
  `open_tape_reader`, `RawTapeWriter`) from top-level
  `src/retracesoftware/tape.py`. `io.recorder()` / `io.replayer()` build
  a `System` from `proxy/system.py`, which delegates `patch_type` /
  `unpatch_type` to `proxy/patchtype.py`, uses `proxy/proxytype.py`
  (which reaches `proxy/stubfactory.py`), uses `proxy/typeutils.py`
  via `patchtype.py`, and uses `install/patcher.py` and
  `install/edgecases.py`. As of `d9181fb`, `system.py` also imports
  `ext_gateway` / `int_gateway` factory names from `proxy/gateway.py`,
  but those factories are not yet defined there — the gateway split is
  in-progress refactor work, not finished CLI behavior. See "In-progress"
  above. **That is the current CLI proxy path.**
- **Test / `proxy/mode/` surface:**
  The `contexts.py` / `context.py` / `_system_specs.py` /
  `_system_patching.py` / `_system_adapters.py` cluster drives most of
  `tests/proxy/` and the in-progress `proxy/mode/` package. Editing it
  changes test and `mode/` behavior; it does not change CLI record/replay
  behavior.
- `retracesoftware.protocol.replay` (the top-level `protocol/` package,
  not `proxy/`) handles in-memory / test readers and monitoring. Inspect
  it whenever a task touches monitoring or in-memory replay readers in
  addition to `io.py`.
- The active tests under `tests/proxy/` are: `test_patch.py`,
  `test_monitoring.py`, `test_system_io_tape.py`,
  `test_system_direct_context.py`, `test_system_unpatch.py`,
  `test_system_adapter.py`, `test_replay_materialize.py`,
  `test_io_memory_tape.py`. (`test_system_context.py` is
  `pytest.mark.skip`ped — not a source of truth.)

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
3. Trace the CLI runtime path before changing code:
   `__main__.py` -> `proxy/tape.py` (Protocol types) +
   top-level `src/retracesoftware/tape.py` (recording I/O) +
   `proxy/io.py` -> `proxy/system.py` (with `proxytype.py`, `typeutils.py`,
   and `install/`). The test/`mode/` cluster (`contexts.py`, `context.py`,
   `_system_specs.py`, `_system_patching.py`, `_system_adapters.py`) is
   not on this path — following it while debugging a CLI bug leads to
   fixes in the wrong layer.
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
details. They are the entire CLI proxy surface; changes here can cascade
across record, replay, install patching, and downstream library replay:

- `system.py`
- `patchtype.py` (extracted from `system.py` in `d9181fb`)
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

These files exist and look authoritative, but they are rarely (or never) the
right place to fix a record/replay or boundary bug:

- `proxytype.py`
  Proxy _type construction_. Editing it has historically broken
  serialization while a one-line fix in the responsible handler in
  `system.py` or `io.py` would have been correct. If a fix lands here,
  justify why type construction itself is wrong rather than the per-call
  wrapping or message flow.
- `contexts.py`, `context.py`, `_system_specs.py`, `_system_patching.py`,
  `_system_adapters.py`
  **Test-only / `proxy/mode/`-only.** Not on the CLI runtime path. They
  drive `tests/proxy/` behavior and `proxy/mode/` semantics; editing them
  does not change CLI record/replay. Confirm whether the bug is reported
  against the CLI or against tests/`mode/` before editing.
- `tests/proxy/test_system_context.py`
  Marked `pytest.mark.skip`. Not a source of truth for current behavior.

When in doubt, fix the smallest layer that owns the contract being violated:
module config (`src/retracesoftware/modules/*.toml`) -> install patcher ->
proxy handler (`io.py`) -> proxy kernel (`system.py`). Move outward only
when the inner layer cannot express the fix.

## Working Rules

- Before changing proxy code, restate the relevant `DESIGN.md` expectation:
  what gate should run, what should be recorded or replayed, and what must
  stay aligned.
- Verify real CLI call sites from `src/retracesoftware/__main__.py`,
  `src/retracesoftware/tape.py` (top-level recording I/O),
  `src/retracesoftware/proxy/tape.py` (Protocol types only),
  `src/retracesoftware/proxy/io.py`, `src/retracesoftware/proxy/system.py`,
  `src/retracesoftware/proxy/patchtype.py`,
  `src/retracesoftware/proxy/proxytype.py`,
  `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`),
  and `src/retracesoftware/install/`. The test/`mode/` cluster
  (`contexts.py`, `context.py`, `_system_specs.py`,
  `_system_patching.py`, `_system_adapters.py`) is not on the CLI
  runtime path. `proxy/gateway.py` is in-progress refactor scaffolding
  with currently unresolved cross-imports to `system.py` — see
  "In-progress" above before relying on it.
- If a fix can live in `src/retracesoftware/modules/*.toml` or the install
  layer, prefer that over rewriting boundary logic.
- If you change `system.py`, `patchtype.py`, `io.py`, `proxytype.py`,
  `typeutils.py`, or either `tape.py`, explicitly call out the
  determinism impact and explain how the change preserves gate
  selection, binding semantics, and message alignment.
- If a change is being proposed in `contexts.py`, `context.py`,
  `_system_specs.py`, `_system_patching.py`, `_system_adapters.py`, or
  `proxy/mode/`, first confirm the bug actually belongs to the test or
  `mode/` surface — not the CLI. If it is a CLI bug, the fix almost
  certainly belongs in `system.py`, `io.py`, `install/`, or
  `modules/*.toml` instead.
- If you change replay message parsing, binding close consumption, thread
  demux, or materialization flow, check `tests/proxy/test_system_io_tape.py`,
  `tests/proxy/test_replay_materialize.py`, and the relevant install-level
  replay regressions before considering the patch safe.
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

- `src/retracesoftware/proxy/DESIGN.md` (rewritten in `d9181fb` —
  describes the **target** gateway-split architecture; ahead of the
  current code, see "In-progress" above)
- `src/retracesoftware/__main__.py`
- `src/retracesoftware/tape.py` (top-level — recording I/O implementation)
- `src/retracesoftware/proxy/tape.py` (Protocol types only)
- `src/retracesoftware/proxy/io.py`
- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/proxy/patchtype.py` (extracted from `system.py`
  in `d9181fb`; owns `patch_type` / `unpatch_type`)
- `src/retracesoftware/proxy/proxytype.py`
- `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`)
- `src/retracesoftware/proxy/stubfactory.py` (reached via `proxytype.py`)
- `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

Test-only / `proxy/mode/`-only (not on CLI runtime path):

- `src/retracesoftware/proxy/contexts.py`
- `src/retracesoftware/proxy/context.py`
- `src/retracesoftware/proxy/_system_specs.py`
- `src/retracesoftware/proxy/_system_patching.py`
- `src/retracesoftware/proxy/_system_adapters.py`

In-progress (not wired into the CLI runtime today):

- `src/retracesoftware/proxy/mode/`
- `src/retracesoftware/proxy/gateway.py` (gateway-split scaffolding —
  `DESIGN.md` describes a target where `gateway.py` exports
  `ext_gateway` / `int_gateway` factories that `system.py:27` imports;
  the factories do not exist in `gateway.py` yet, and the cross-imports
  between `system.py` and `gateway.py` currently do not resolve. Do
  not treat as finished live kernel.)

Top-level protocol package (live, distinct from anything under `proxy/`):

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
