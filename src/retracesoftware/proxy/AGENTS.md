# Proxy Layer

This directory implements the record/replay boundary. Code here decides what
crosses between deterministic "internal" code and nondeterministic "external"
code, what gets written to the trace, what gets replayed from the trace, and
how divergence is detected. Small changes here can silently corrupt replay.

`AGENTS.md` is the **operational** guide: rules, process, where to look, how
to debug. `DESIGN.md` is the **behavior contract** for the proxy kernel: what
the system is supposed to do, what messages flow, what gateways exist, and
why. When they overlap, `DESIGN.md` wins. Whenever this file says "see
`DESIGN.md` -> $section", that pointer is the authoritative answer; do not
re-derive the contract from this file.

## Hard Rules (Non-Negotiable)

1. **Replay never calls the live external callable, when retrace is enabled.**
   Replay reads the recorded `RESULT` or `ERROR` from the message stream and
   returns or raises the recorded outcome. If a fix appears to require running
   real external code during normal replay, the diagnosis is wrong.
   See `DESIGN.md` -> "Allocation And Materialization".
2. **Control-plane work must bypass the gates** via `disable()` or
   `disable_for()`. Recording debugger, monitoring, or replay plumbing into
   the trace causes silent desynchronization.
   See `DESIGN.md` -> "`disable()` / `disable_for()`".
3. **Message order is contract.** Do not introduce, drop, reorder, or coalesce
   `CALLBACK`, `RESULT`, `ERROR`, `CHECKPOINT`, bind open/close, or
   thread scheduler events. Replay alignment is a hard invariant.
   See `DESIGN.md` -> "Record Versus Replay" and "Important Invariants".
4. **Prefer the narrowest fix in the responsible handler.** If a single diff
   touches more than one of `system.py`, `GatewayPair`, `proxyfactory2.py`,
   `proxytypefactory2.py`, `typeextender.py`, or `io.py`, stop and re-read
   `DESIGN.md` before continuing.
   See `DESIGN.md` -> "Main Pieces".
5. **Do not backdoor proxy semantics through concrete implementation checks.**
   Boundary classification belongs to the proxy contract. Outside the owning
   proxy code path, designated adapters, and focused type-behavior tests, do
   not use `isinstance`, `issubclass`, exact `type(...)` checks, module names,
   class-name strings, private attributes, `repr`, or object identity to infer
   whether something is proxied, patched, bound, internal, external, a trace
   message, or replay materialization state. If another layer needs that
   answer, expose the narrowest semantic predicate/API from the owner instead.
6. **Do not add backwards-compatibility shims** for old trace formats,
   message tags, or removed APIs. If a recording no longer matches the
   current code, regenerate the recording. Do not pattern new code after
   anything you find under `proxy/` that is not listed in "Live Files" below.
7. **Prioritize simplicity above all else.** When two correct designs
   exist, pick the smaller one. Do not add abstractions, indirection, or
   "extensibility hooks" without a concrete consumer that needs them.
   Deletion is preferred over generalization.
8. **Tracing is observational; it must never alter thread scheduling.**
   See `DESIGN.md` -> "Threads" -> "Cross-thread synchronization".
   Smallest reproducer:
   `tests/install/stdlib/test_threading_lock_replay_regression.py`.
   If it fails, the portal/web replay regressions in the Proxy Kernel
   Sentinel Bundle are likely downstream.
9. **Fix divergence causes, not exposed symptoms.** The mandatory replay
   debugging loop is:
   1. Find the fundamental record/replay divergence.
   2. Build the smallest failing regression that reproduces that divergence.
   3. Fix the shared cause named by the relevant `DESIGN.md` contract.
   If replay has recorded messages for a logical thread that replay never
   starts, the primary bug is the nondeterministic branch that controlled
   thread birth or wakeup. Do not add a library-specific patch just because the
   failure surfaced in a dockertest. See `DESIGN.md` -> "Threads" ->
   "Cross-thread synchronization".

## Required Pre-Edit Statement

Before editing any file under `src/retracesoftware/proxy/`, state in chat:

1. The `DESIGN.md` rule that should hold (quote or paraphrase the relevant
   contract: gate, phase, callback routing, binding, materialization, or
   message order). If you cannot quote one, re-read `DESIGN.md` first.
2. The first observed mismatch (file:line plus what gate, phase, or message
   is wrong).
3. The narrowest fix layer and why the fix does NOT belong one layer further
   out (module config in `src/retracesoftware/modules/*.toml`, install
   patcher, proxy handler, or proxy kernel).
4. The semantic question the code needs answered and the existing API that
   answers it. If no such API exists, state which layer owns the new minimal
   API and why.
5. Why the fix does not inspect concrete types, private attributes, module
   names, class-name strings, or object identity from another layer to recover
   semantic meaning.
6. Which sentinel tests from "Sentinel Tests" below will be re-run.
7. The smallest failing regression you will add or update for the fundamental
   divergence. For any missing/extra-thread failure, this must be a stdlib or
   local-module reproducer that proves the thread scheduling decision itself
   diverges.

If steps 1-5 cannot be completed from `DESIGN.md` and the live runtime path,
re-read `DESIGN.md` and trace the call flow again before editing. Do not
"try a fix" in `system.py`.

## Read Order

Before editing proxy-layer code, read these in order:

1. This file (`proxy/AGENTS.md`).
2. `proxy/DESIGN.md` end-to-end. The whole document is the behavior contract;
   `## Main Pieces`, `## Record Versus Replay`, `## Object Categories At The
   Boundary`, `## Binding`, `## Allocation And Materialization`,
   `## Threads`, and `## Reading The Code` are the most frequently-needed
   sections.
3. The current CLI runtime path on disk, in this order:
   - `src/retracesoftware/__main__.py`
   - `src/retracesoftware/tape.py` (top-level — recording I/O implementation)
   - `src/retracesoftware/proxy/tape.py` (Protocol types only)
   - `src/retracesoftware/proxy/traceio.py`
   - `src/retracesoftware/proxy/taggedtraceio.py`
   - `src/retracesoftware/proxy/io.py`
   - `src/retracesoftware/proxy/system.py`
   - `src/retracesoftware/gateway/_gatewaypair.py`
   - `src/retracesoftware/proxy/proxyfactory2.py`
   - `src/retracesoftware/proxy/proxytypefactory2.py`
   - `src/retracesoftware/proxy/typeextender.py`
   - `src/retracesoftware/proxy/typeutils.py`
   - `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

## Live Files

Verified against the actual import graph. **Only files in this section are
considered live for AI guidance purposes.** If a file under `proxy/` is not
listed here, do not pattern new code after it and do not assume it
influences runtime behavior. The "what each file does" lives in `DESIGN.md`;
this section is just the import-graph-verified list with one-line operational
notes.

### Live CLI runtime kernel (record + replay both)

| File | One-line role | Authoritative section |
|---|---|---|
| `system.py` | Owns `System`, the phase gate thread-local, gateway factories, `run()`, `disable`, `disable_for`, `patch`, `patch_function`. | `DESIGN.md` -> "Main Pieces", "Threads" |
| `src/retracesoftware/gateway/_gatewaypair.py` | Current paired record/replay gateway wiring for internal/external crossings. | `DESIGN.md` -> "How The Two Gateways Interact" |
| `proxyfactory2.py` | System-facing proxy factory: internal/external proxy construction, `from_spec` callback binding, replay materialization. | `DESIGN.md` -> "Main Pieces" |
| `proxytypefactory2.py` | Dynamic internal/external proxy type generation and extended-type companions. | `DESIGN.md` -> "Internal Retrace: Using The Boundary On Ourselves" |
| `typeextender.py` | Retrace-owned extended types, generated `__new__`/`__init__`, subclass callback override handling. | `DESIGN.md` -> "Object Categories At The Boundary" |
| `io.py` / `messagestream.py` / `traceio.py` / `taggedtraceio.py` | `recorder()` / `replayer()` builders plus semantic trace messages, tagged trace encoding/decoding, replay parsing, binding resolution, scheduler ordering, and disabled-context real-object support. | `DESIGN.md` -> "Record Versus Replay" and "Allocation And Materialization" |
| `tape.py` (proxy) | `Tape` / `TapeReader` / `TapeWriter` `Protocol` declarations only (~40 lines). | n/a — interface declarations |
| `typeutils.py` | `WithoutFlags`, `modify`. Used by install/type utility paths. | n/a — utility |
| `stubfactory.py` | Replay-time stub generation. Reached only via `proxytype.py`. | `DESIGN.md` -> "Allocation And Materialization" |

Top-level files reached from `__main__.py`:

- `src/retracesoftware/tape.py` — the actual recording I/O implementation
  (different from `proxy/tape.py`, which is just Protocol declarations).
- `src/retracesoftware/protocol/replay.py` — in-memory / monitoring replay
  readers, lives outside `proxy/` but co-evolves with `io.py`.

### Dead / unimported (do not pattern after, do not import)

These files exist on disk but have **zero importers** in `src/` or `tests/`.
Scheduled for cleanup; do not extend them.

- `_binding_checkpoint.py`, `_system_patching.py`, `globalref.py`,
  `proxyfactory.py`, `serializer.py`.
- `protocol.py` — Protocol/ABC declarations only; referenced from `docs/`,
  not from runtime code.
- `contexts.py` — re-introduced in `057ce2b` but **currently unimportable**:
  imports `from .context import ...` and `from ._system_specs import ...`,
  neither target module exists on disk. Treat as a design sketch, not live
  wiring. Do not delete without coordinating with the author; do not import.

## Code-Level Facts Not In DESIGN.md

`DESIGN.md` describes the contract; the items below are concrete code-level
identities that live in this layer and are easy to mis-edit. They exist here
because they are too implementation-specific for `DESIGN.md` but stable
enough that AI agents need them visible.

- **`system.is_bound` is not the same as `system.bind(obj)`.**
  `system.is_bound` is a public `utils.WeakSet` exposed on `System`;
  `system.is_bound.add(obj)` silently marks `obj` as internal. `system.bind(obj)`
  does that **plus** fires `on_bind` (which during recording writes a
  `NewBinding` to the wire). `__main__._bind_record_runtime` deliberately
  uses `system.is_bound.add(obj)` for tape/recorder plumbing that must
  never appear on the wire — confusing the two is a determinism bug.
- **`disable_for(fn, retrace=False)` returns a `disabled_callable` instance**,
  not just a closure with a stamp. It clears the gate while `fn` is called.
  With the default `retrace=True`, `disable_for(fn)` wraps that disabled
  callable in `retrace.disable` when retrace-python is loaded, so even the
  wrapper frame does not perturb coordinates/thread ids. The install patcher
  uses `retrace=False` for TOML `disable` entries because those are
  application/library passthroughs, not control-plane bodies.
  The gate-only `disabled_callable` subclasses `wrapped_callable`, so the same
  non-binding behavior applies.
- **`patch_function()` returns a `wrapped_callable`** so module-level wrapped
  functions don't bind as methods when accessed through a class.
- **`patch_type` is idempotent within the same `System`.** If the class is
  already in `system.patched_types` (because a base type config patched it
  via subclass walk and a later config names the subclass directly), the
  second call returns `cls` immediately. Cross-`System` re-patching still
  raises. Sentinel:
  `tests/proxy/test_proxy_runtime.py::test_patch_type_is_idempotent_for_subtypes_patched_through_base`.
- **`io.replayer()` swaps `system.ext_gateway_factory` to
  `functional.partial(ext_replay_gateway, ext_execute)` and
  `system.int_gateway_factory` to `int_replay_gateway`** before user code
  runs. `System.run()` then installs whatever the factories return into
  the per-thread gateway slots. This is why the same patched call sites
  do completely different things in record vs replay.
- **`io.replayer()` installs replay bind helpers instead of plain
  `system.bind`.** `bind_replay_object()` loops over
  `tape_reader.bind(obj)` and on `ExpectedBindMarker` consumes replay-side
  callback/completion/result/checkpoint messages until the recorded bind
  marker appears. `system.async_new_patched` uses that helper without
  executing skipped stub-helper callbacks; `_on_alloc` uses it with
  `run_raw_callback` for real internal callback envelopes. After a successful
  bind, the object is also added to `system.is_bound` so a later use does not
  re-bind it. Sentinels:
  `tests/proxy/test_system_io_tape.py::test_replayer_skips_standalone_callback_result_before_next_call`,
  `tests/proxy/test_system_io_tape.py::test_replayer_skips_stub_callback_before_async_new_patched_bind`.
- **`io.py` scheduler stream owns replay thread ordering.** It is a peekable
  global stream that consumes or defers `THREAD_SWITCH`, applies the recorded
  cursor delta to the previous scheduled thread, arms `retrace.call_at(...)`
  from recorded cursors, and uses `ThreadHandoff.to(...)` only at actionable
  replay scheduling points. Do not reintroduce per-thread replay queues here.

## Debugging Retrace + Stacktraces

This section is the operational debugging guide. For the *mechanics* of
stacktrace messages, callback envelopes, bind markers, and message ordering,
see `DESIGN.md` -> "Record Versus Replay" -> "Record" / "Replay" and
`DESIGN.md` -> "Important Invariants".

### Why retrace stacktraces matter

Retrace is a deterministic record-and-replay system. When replay diverges
from record, the failure surface is almost always far away from the actual
cause: replay continues consuming messages until it hits something that
cannot match, and only then raises. The recorded stream is your only
authoritative log of what record actually did.

A retrace stacktrace tells you three things at once:

1. **Which gateway and phase produced the event** — was the system in
   `internal` (running user/library Python) or `external` (running real
   nondeterministic code) when this happened?
2. **Which boundary message was being emitted or consumed** — `CALLBACK`,
   `RESULT`, `ERROR`, `CHECKPOINT`, `NEW_BINDING`, `BINDING_DELETE`,
  `THREAD_SWITCH`, `STACKTRACE`.
3. **Where in the wire stream the failure occurred** — sequence number plus
   thread id, which lets you find the matching record-side event.

If you cannot map a failure to a (gate, phase, message) triple, you do not
yet understand the bug. Stop and re-trace.

### Workflow when a proxy bug is reported

1. **State the contract.** Quote or paraphrase the `DESIGN.md` rule that
   should hold for the failing scenario: which gate should run, which
   phase, what message comes next, what binding/materialization step is
   expected. If you cannot find the rule, re-read `DESIGN.md`.
2. **Find the fundamental divergence.** Wrong gate? Wrong phase? A
   message consumed at the wrong index? A bind that did not happen, or
   one that fired twice? An external call entering the gateway in the
   `external` phase? Materialization in a context where it should have
   been a normal patched call? A logical thread present in the trace but
   not started during replay? The first mismatch is where to look; later
   symptoms are downstream noise.
3. **Build the smallest failing regression.** Prefer a stdlib or local-module
   reproducer over the original dockertest/application. The regression should
   prove the divergent contract directly, not merely assert the high-level
   library scenario still fails.
4. **Trace the CLI runtime path before editing.** `__main__.py` ->
   tape/trace I/O -> `proxy/system.py` -> `GatewayPair` ->
   `proxyfactory2.py` / `proxytypefactory2.py` / `typeextender.py` ->
   `install/`. For "what does the boundary actually do to arguments and
   hooks?", `GatewayPair` is the source of truth. For "how do types get
   extended or wrapped?", `proxytypefactory2.py` and `typeextender.py` are.
   For "how is the kernel wired and how does `run()` enter the internal
   space?", `system.py` is.
5. **Pick the narrowest fix layer.** Module config (`modules/*.toml`) ->
   install patcher -> gateway pipeline / `io.py` hook -> kernel
   (`system.py`). Move outward only when the inner layer cannot express
   the fix.
6. **Re-run the matching sentinel tests** (see "Sentinel Tests" below)
   before declaring the fix done.

### How to read retrace debug output

When a recording or replay is run with `RETRACE_CONFIG=debug`, lines like:

```
Retrace(<pid>) - ObjectWriter[<seq>] -- <event>
Retrace(<pid>) - ObjectWriter[<seq>] -- <payload>
```

show the wire-format event sequence on the writer side. Useful operational
patterns:

- `NEW_BINDING` followed by a small int is record assigning a stable handle
  to a live object. Replay later resolves that handle back to a live
  object via `_ReplayBindingState`.
- `BINDING_DELETE` is record telling replay it can drop a handle
  (weakref-style cleanup). Misalignment here usually means GC ordering
  diverged between record and replay.
- zero or more `CALLBACK` / `CALLBACK_RESULT` pairs may appear before the
  next `RESULT` or `ERROR`. Anything that breaks this nesting on replay is a
  desync.
- `THREAD_SWITCH` with stable `_thread.get_ident()` ids drives replay
  scheduling. If a thread emits outbound messages without the recorded switch
  path needed to hand execution to and from that thread, scheduling is wrong.
- `STACKTRACE` is emitted by `--stacktraces` mode and is replay-significant
  in that mode (it is a recorded message, not incidental logging).
  See `DESIGN.md` -> "Important Invariants".

### Common failure shapes and what they usually mean

- **`object is already bound` during record** — record-side bind contract
  violation. Usually `system.bind(obj)` was called twice for the same
  object, or `system.bind` was called on something already added to
  `system.is_bound`. Look at the call path that produced the second
  bind. (`DESIGN.md` -> "Binding".)
- **Replay subprocess hangs / `subprocess.TimeoutExpired`** — replay is
  waiting for a message that never arrives. Almost always means record
  and replay disagree about how many messages a thread should consume,
  often because a `THREAD_SWITCH` handoff is missing or misrouted, or because
  a callback envelope was emitted but is being expected
  as an outbound call (or vice versa). Check `SchedulerStream` cursor arming
  and global stream position.
- **`Could not read N bytes from tracefile with timeout`** — same
  underlying cause as the previous point: replay is blocked waiting for
  the global stream to reach the event a thread expects. The thread mentioned
  in the traceback is the one whose expectations are wrong.
- **Replay diverges silently and only later crashes with an unrelated
  Python exception** — message alignment broke much earlier; the visible
  crash is downstream. `--stacktraces` mode + `--monitor` mode help
  pinpoint the first divergence. Find the first index where record and
  replay event sequences differ.
- **`patch_type: X is already patched by another System instance`** — two
  distinct `System` instances tried to patch the same class. Either you
  are running record/replay in the same process without proper teardown,
  or a test is leaking state from a previous case. Same-`System` re-patch
  is now a no-op (see `patch_type` idempotency above).

## Where Bugs Usually Are NOT

These files exist and look authoritative, but they are rarely (or never)
the right place to fix a record/replay or boundary bug:

- `proxytype.py` — Proxy *type construction*. Editing it has historically
  broken serialization while a one-line fix in the responsible gateway
  pipeline (`GatewayPair`) or `io.py` hook would have been correct. If a
  fix lands here, justify why type construction itself is wrong rather
  than the per-call wrapping or message flow.
- `stubfactory.py` — Replay-time stub generation. Reached via
  `proxytype.py`. Bugs here manifest at materialization time, but the
  cause is almost always in `_on_alloc`, `async_new_patched`,
  `bind_replay_object`, or replay-side unbound lock/RLock handling, not in
  stub construction itself.
- Anything in "Dead / unimported" above.

## High-Risk Hazards

These are the recurring shapes of replay-breaking bugs. Treat any change
that touches one of these areas as requiring sentinel-test re-runs.

- Set iteration or unstable dict-order assumptions in replay-sensitive
  paths.
- `id()`, `hash()`, memory addresses, or object identity used for
  ordering.
- Weakref callbacks, `__del__`, GC timing, or cleanup side effects that
  can run in a different order during replay.
- Thread creation, locks, synchronization, and thread-id handling.
- `fork()` behavior, pid switching, parent/child trace handling.
- New nondeterministic library behavior that is not intercepted at the
  boundary.
- I/O or logging in replay/control-plane paths that does not bypass the
  gates.
- Changes to wrapping, unwrapping, walker logic, stub refs, or
  materialization without tests for nested structures and callback
  paths.
- Changes to when `RESULT` / `ERROR` messages are emitted or consumed.
- Callback exceptions: changes that swallow, rewrap, or reroute callback
  exceptions can break replay parity.

## Sentinel Tests

The change-to-test mapping below is the minimum re-run set. If you change
something on the left, run the tests on the right before declaring the
patch done.

| If you change... | Re-run |
|---|---|
| `system.py` (`run`, `disable`, `disable_for`, `bind`, `patch_function`, `wrap_async`, `disabled_callable`/`wrapped_callable`) | `tests/proxy/test_system_io_tape.py`, `tests/install/stdlib/test_threading_lock_replay_regression.py`, `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`, `tests/test_record_replay.py::test_record_then_replay_asyncio_run_coroutine_threadsafe` |
| `GatewayPair` (record/replay gateway wiring, passthrough predicates, argument/result transforms) | `tests/gateway/test_gatewaypair.py`, `tests/proxy/test_system.py`, `tests/test_main_memory_tape.py` |
| `proxytypefactory2.py` / `typeextender.py` (dynamic proxy types, extended types, subclass callbacks, constructor/bind behavior) | `tests/proxy/test_proxytypefactory2.py`, `tests/proxy/test_proxyfactory2.py`, `tests/proxy/test_system.py` |
| `io.py` / `messagestream.py` replay parsing, scheduler/binding streams, `equal()` markers, `bind_replay_object`, `consume_callback_completion`, disabled-context live-call bypass | `tests/proxy/test_system_io_tape.py`, `tests/proxy/test_proxy_runtime.py`, `tests/install/stdlib/test_threading_lock_replay_regression.py` |
| `proxytype.py` / `stubfactory.py` (type construction, stub generation) | `tests/proxy/test_proxy_runtime.py`, `tests/proxy/test_system_io_tape.py` |
| Monitoring or compatibility replay-reader behavior | `tests/proxy/test_monitoring.py`, plus inspect `src/retracesoftware/protocol/replay.py` |
| Anything that affects exception-in-callback round-tripping | `tests/proxy/test_system_io_tape.py`, `tests/test_record_replay.py` |

When in doubt, run all four `tests/proxy/test_*.py` files plus
`tests/test_main_memory_tape.py` and `tests/install/test_hash_patching.py`
— that whole set takes ~5 seconds and covers the kernel paths.

## References

Live CLI runtime path (verified by import graph):

- `src/retracesoftware/proxy/DESIGN.md` (the behavior contract)
- `src/retracesoftware/__main__.py`
- `src/retracesoftware/tape.py` (top-level — recording I/O implementation)
- `src/retracesoftware/proxy/tape.py` (Protocol types only)
- `src/retracesoftware/proxy/traceio.py`
- `src/retracesoftware/proxy/taggedtraceio.py`
- `src/retracesoftware/proxy/io.py`
- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/gateway/_gatewaypair.py`
- `src/retracesoftware/proxy/proxyfactory2.py`
- `src/retracesoftware/proxy/proxytypefactory2.py`
- `src/retracesoftware/proxy/typeextender.py`
- `src/retracesoftware/proxy/typeutils.py`
- `src/retracesoftware/proxy/stubfactory.py` (reached via `proxytype.py`)
- `src/retracesoftware/install/` (`patcher.py`, `edgecases.py`)

Top-level protocol package (live, distinct from anything under `proxy/`):

- `src/retracesoftware/protocol/replay.py`

Active proxy unit tests:

- `tests/proxy/test_io_memory_tape.py`
- `tests/proxy/test_monitoring.py`
- `tests/proxy/test_proxy_runtime.py`
- `tests/proxy/test_system_io_tape.py`

Adjacent tests touching this layer:

- `tests/test_main_memory_tape.py`
- `tests/install/test_hash_patching.py`
- `tests/install/stdlib/test_threading_lock_replay_regression.py`
- `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`
- `tests/install/external/test_anyio_task_group_random_replay_regression.py`
- `tests/install/stdlib/test_random.py`
- `tests/test_record_replay.py::test_record_then_replay_asyncio_run_coroutine_threadsafe`

Docs:

- `docs/DEBUGGING.md` — full debugging guide for the whole CLI, beyond proxy.
- `docs/THREAD_REPLAY.md` — thread-routing details that supplement
  `DESIGN.md` -> "Threads".
