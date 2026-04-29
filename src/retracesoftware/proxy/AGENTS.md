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
   returns or raises the recorded outcome. The only materialized-real-object
   exception is when retrace is already disabled and replay needs a local
   runtime object such as a module lock. If a fix appears to require running
   real external code during normal replay, the diagnosis is wrong.
   See `DESIGN.md` -> "Allocation And Materialization".
2. **Materialized objects are only for disabled-context runtime needs**, not
   general external-object reconstruction.
   See `DESIGN.md` -> "Allocation And Materialization".
3. **Control-plane work must bypass the gates** via `disable_for()`. Recording
   debugger, monitoring, or replay plumbing into the trace causes silent
   desynchronization.
   See `DESIGN.md` -> "`disable_for()`".
4. **Message order is contract.** Do not introduce, drop, reorder, or coalesce
   `CALL`, `CALLBACK`, `RESULT`, `ERROR`, `CHECKPOINT`, bind open/close, or
   `THREAD_SWITCH` events. Replay alignment is a hard invariant.
   See `DESIGN.md` -> "Record Versus Replay" and "Important Invariants".
5. **Prefer the narrowest fix in the responsible handler.** If a single diff
   touches more than one of `system.py`, `gateway.py`, `patchtype.py`,
   `io.py`, or `proxytype.py`, stop and re-read `DESIGN.md` before continuing.
   See `DESIGN.md` -> "Main Pieces".
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
4. Which sentinel tests from "Sentinel Tests" below will be re-run.

If steps 1-3 cannot be completed from `DESIGN.md` and the live runtime path,
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
   - `src/retracesoftware/proxy/io.py`
   - `src/retracesoftware/proxy/system.py`
   - `src/retracesoftware/proxy/gateway.py`
   - `src/retracesoftware/proxy/patchtype.py`
   - `src/retracesoftware/proxy/proxytype.py`
   - `src/retracesoftware/proxy/typeutils.py` (reached via `patchtype.py`)
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
| `system.py` | Owns `System`, the phase gate thread-local, gateway factories, `run()`, `disable_for`, `wrap_start_new_thread`, `patch`, `patch_function`. | `DESIGN.md` -> "Main Pieces", "Threads" |
| `gateway.py` | Pure factories: `ext_gateway`, `int_gateway`, `ext_replay_gateway`, `int_replay_gateway`, plus `ext_runner`, `int_runner`, `unproxy_ext`, `unproxy_int`. Zero proxy-internal deps. | `DESIGN.md` -> "How The Two Gateways Interact" |
| `patchtype.py` | In-place type-patching: `patch_type`, `unpatch_type`, subclass walk, alloc-hook installation, `__init_subclass__` rewriting. | `DESIGN.md` -> "Object Categories At The Boundary" -> "Patched objects and patched types" |
| `io.py` | `recorder()` / `replayer()` builders. Hosts `_RawTapeSource`, `_ReplayBindingState`, `_ThreadDemuxSource`, `_IoMessageSource`, replay-time bind parsing, and disabled-context real-object support. | `DESIGN.md` -> "Record Versus Replay" and "Allocation And Materialization" |
| `tape.py` (proxy) | `Tape` / `TapeReader` / `TapeWriter` `Protocol` declarations only (~40 lines). | n/a — interface declarations |
| `proxytype.py` | `DynamicProxy`, `superdict`, `dynamic_proxytype`, `dynamic_int_proxytype`. Reached via `system.py` and `patchtype.py`. | `DESIGN.md` -> "Internal Retrace: Using The Boundary On Ourselves" -> "Proxy generation through retrace" |
| `typeutils.py` | `WithoutFlags`, `modify`. Used by `patchtype.py` and `install/patcher.py`. | n/a — utility |
| `stubfactory.py` | Replay-time stub generation. Reached only via `proxytype.py`. | `DESIGN.md` -> "Allocation And Materialization" |

Top-level files reached from `__main__.py`:

- `src/retracesoftware/tape.py` — the actual recording I/O implementation
  (different from `proxy/tape.py`, which is just Protocol declarations).
- `src/retracesoftware/protocol/replay.py` — in-memory / monitoring replay
  readers, lives outside `proxy/` but co-evolves with `io.py`.

### Dead / unimported (do not pattern after, do not import)

These files exist on disk but have **zero importers** in `src/` or `tests/`.
Scheduled for cleanup; do not extend them.

- `_binding_checkpoint.py`, `_system_patching.py`, `_system_threading.py`,
  `globalref.py`, `proxyfactory.py`, `serializer.py`, `startthread.py`.
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
- **`disable_for(fn)` returns a `disabled_callable` instance**, not just a
  closure with a stamp. Thread startup detects it via
  `isinstance(target, disabled_callable)` (see `_is_disabled_thread_target`)
  and skips `Thread.start()`'s `_started.wait()` and similar bootstrap. Do
  not unwrap the `disabled_callable` before passing it to
  `Thread(target=...)`. `disabled_callable` subclasses `wrapped_callable`,
  so the same non-binding behavior applies.
- **`patch_function()` returns a `wrapped_callable`** so module-level wrapped
  functions don't bind as methods when accessed through a class.
- **`patch_type` is idempotent within the same `System`.** If the class is
  already in `system.patched_types` (because a base type config patched it
  via subclass walk and a later config names the subclass directly), the
  second call returns `cls` immediately. Cross-`System` re-patching still
  raises. Sentinel:
  `tests/proxy/test_replay_materialize.py::test_patch_type_is_idempotent_for_subtypes_patched_through_base`.
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
- **`io.py` `_ThreadDemuxSource` re-raises `KeyboardInterrupt` and
  `SystemExit` cleanly** instead of swallowing them as desync. Keep this
  contract; it is how Ctrl-C escapes a recording.

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
2. **Which boundary message was being emitted or consumed** — `CALL`,
   `CALLBACK`, `RESULT`, `ERROR`, `CHECKPOINT`, `NEW_BINDING`,
   `BINDING_DELETE`, `THREAD_SWITCH`, `STACKTRACE`.
3. **Where in the wire stream the failure occurred** — sequence number plus
   thread id, which lets you find the matching record-side event.

If you cannot map a failure to a (gate, phase, message) triple, you do not
yet understand the bug. Stop and re-trace.

### Workflow when a proxy bug is reported

1. **State the contract.** Quote or paraphrase the `DESIGN.md` rule that
   should hold for the failing scenario: which gate should run, which
   phase, what message comes next, what binding/materialization step is
   expected. If you cannot find the rule, re-read `DESIGN.md`.
2. **Find the first observed mismatch.** Wrong gate? Wrong phase? A
   message consumed at the wrong index? A bind that did not happen, or
   one that fired twice? An external call entering the gateway in the
   `external` phase? Materialization in a context where it should have
   been a normal patched call? The first mismatch is where to look; later
   symptoms are downstream noise.
3. **Trace the CLI runtime path before editing.** `__main__.py` ->
   `proxy/io.py` -> `proxy/system.py` -> `proxy/gateway.py` (with
   `proxy/patchtype.py`, `proxy/proxytype.py`, and `install/`). For
   "what does the boundary actually do to arguments and hooks?",
   `gateway.py` is the source of truth. For "how do types get patched?",
   `patchtype.py` is. For "how is the kernel wired and how does `run()`
   install gateways?", `system.py` is.
4. **Pick the narrowest fix layer.** Module config (`modules/*.toml`) ->
   install patcher -> gateway pipeline / `io.py` hook -> kernel
   (`system.py`). Move outward only when the inner layer cannot express
   the fix.
5. **Re-run the matching sentinel tests** (see "Sentinel Tests" below)
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
- `CALL` -> (zero or more `CALLBACK` / `CALLBACK_RESULT`) -> `RESULT` or
  `ERROR` is the canonical external-call envelope. Anything that breaks
  this nesting on replay is a desync.
- `THREAD_SWITCH` with a logical thread id changes which per-thread
  message buffer the next event belongs to. If a thread emits its first
  outbound call without a prior `THREAD_SWITCH` to its id, the thread
  routing is wrong.
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
  often because of a missing or misrouted `THREAD_SWITCH`, or because a
  callback envelope was emitted but is being expected as an outbound
  call (or vice versa). Check `_ThreadDemuxSource` per-thread buffers.
- **`Could not read N bytes from tracefile with timeout`** — same
  underlying cause as the previous point: a thread is blocked waiting
  for messages on a per-thread queue that has nothing for it. The
  thread mentioned in the traceback is the one whose expectations are
  wrong.
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
  pipeline (`gateway.py`) or `io.py` hook would have been correct. If a
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
- Changes to when `CALL` messages are emitted or consumed.
- Callback exceptions: changes that swallow, rewrap, or reroute callback
  exceptions can break replay parity.

## Sentinel Tests

The change-to-test mapping below is the minimum re-run set. If you change
something on the left, run the tests on the right before declaring the
patch done.

| If you change... | Re-run |
|---|---|
| `system.py` (`run`, `wrap_start_new_thread`, `disable_for`, `bind`, `patch_function`, `wrap_async`, `disabled_callable`/`wrapped_callable`) | `tests/proxy/test_system_io_tape.py`, `tests/install/stdlib/test_threading_lock_replay_regression.py`, `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`, `tests/test_record_replay.py::test_record_then_replay_asyncio_run_coroutine_threadsafe` |
| `gateway.py` (any factory, `ext_runner`/`int_runner`, `unproxy_*`, passthrough predicates) | `tests/proxy/test_system_io_tape.py`, `tests/proxy/test_replay_materialize.py`, `tests/test_main_memory_tape.py` |
| `patchtype.py` (subclass walk, `__init_subclass__`, alloc hook, idempotency) | `tests/install/test_hash_patching.py`, `tests/proxy/test_replay_materialize.py::test_patch_type_is_idempotent_for_subtypes_patched_through_base` |
| `io.py` replay parsing, `_ReplayBindingState`, `_ThreadDemuxSource`, `equal()` markers, `bind_replay_object`, `consume_callback_completion`, disabled-context materialization/unbound external support | `tests/proxy/test_system_io_tape.py`, `tests/proxy/test_replay_materialize.py`, `tests/install/stdlib/test_threading_lock_replay_regression.py` |
| `proxytype.py` / `stubfactory.py` (type construction, stub generation) | `tests/proxy/test_replay_materialize.py`, `tests/proxy/test_system_io_tape.py` |
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

Active proxy unit tests:

- `tests/proxy/test_io_memory_tape.py`
- `tests/proxy/test_monitoring.py`
- `tests/proxy/test_replay_materialize.py`
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
