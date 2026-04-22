# Proxy Design

This document describes the current gate-based proxy path implemented by
`system.py` and `io.py`.

## Purpose

The proxy layer is the record/replay boundary.

- Internal code is deterministic user code that should run again during replay.
- External code is nondeterministic library, OS, or C-extension behavior that
  must be intercepted.
- The proxy does not snapshot the whole process. It records boundary crossings
  and replays those crossings while re-executing the Python code around them.

`system.py` is the small kernel that decides how calls cross the boundary.
`io.py` connects that kernel to an actual recording or replay stream.

## Main Pieces

- `System` in `system.py`
  Owns the gates, patches types and functions, creates proxy wrappers, and runs
  user code inside a configured record or replay context.
- `recorder()` in `io.py`
  Builds a `System` whose hooks write protocol events and binding events.
- `replayer()` in `io.py`
  Builds a `System` whose hooks consume recorded events and feed results back
  into the same call sites.
- `stream.Binder`
  Assigns stable handles to live objects and patched types so later messages can
  refer to them.
- Protocol messages
  `ON_START`, `CALL`, `CALLBACK`, `RESULT`, `ERROR`, `CHECKPOINT`,
  `STACKTRACE`, plus bind open/close markers and thread-switch markers.

At the top level, `src/retracesoftware/__main__.py` imports `recorder` and
`replayer`, and the install layer uses `System.patch_type()` to patch selected
stdlib and library types.

## The Core Model: Two Gates

`System` revolves around two thread-local gates:

- `_external`
  Handles internal-to-external calls. This is the path used when user code calls
  a patched C/base method like `socket.recv()` or a patched standalone function.
- `_internal`
  Handles external-to-internal callbacks. This is the path used when external
  code re-enters Python through an override on a patched type family.

The key trick is that both gates are thread-local and cheap to test. When
retrace is inactive, wrapped methods mostly fall through to the original target.
When retrace is active, the gates hold executors that apply recording or replay
logic.

`System.location` exposes the current phase:

- `disabled`: no retrace context is active
- `internal`: running inside retraced Python code
- `external`: currently executing an external call body

## Patching Strategy

### Patching functions

`patch_function()` wraps a standalone callable through `_external`. This is for
module-level functions like `time.time()` that should behave like external
calls.

### Patching types

`patch_type()` mutates a type in place.

It does four things:

1. Wraps the type's callable methods and descriptors so calls route through the
   external gate.
2. Installs an allocation hook on `tp_alloc` so new instances can be bound or
   materialized when retrace is active.
3. Patches Python subclasses of that type so overriding methods route through
   the internal gate.
4. Registers bind support for the type itself so the stream can refer to it.

Only subclass overrides are wrapped as internal callbacks. New subclass-only
methods are skipped because C/base code cannot call methods it does not know
about. That keeps callback routing narrower and cheaper.

## Binding And Proxy Wrappers

The proxy layer has to preserve object identity across the boundary without
serializing whole live objects.

It uses two related mechanisms:

- Binding
  Live objects and patched types get stable binder handles.
- Proxy wrapping
  Values that cannot safely cross as-is are wrapped in generated proxy types.

Important rules:

- Immutable or already-safe values pass through directly.
- Patched types are special: they often trigger allocation/binding flow instead
  of normal wrapping.
- External results may be recorded as lightweight references and materialized on
  replay into live stub objects.

`System._ext_proxy()` and `System._int_proxy()` build the recursive walkers that
decide how arguments and results are transformed as they cross the boundary.

## What Happens During Record

`io.recorder()` builds a `System` and configures its hooks.

### Setup

- A `stream.Binder` tracks object handles.
- The `System` is created with an `on_bind` callback that writes `NEW_BINDING`
  records.
- Hook functions are installed for:
  - lifecycle start (`ON_START`)
  - external call markers or checkpoints
  - callback messages
  - results and errors
  - optional stacktrace deltas

### External call flow

When internal code calls a patched external method:

1. The wrapped method enters `_ext_handler`.
2. If retrace is inactive, it falls back to the real target.
3. If retrace is active, `_external` uses `ext_executor()`.
4. `ext_executor()`:
   - emits call hooks
   - converts internal arguments to external form
   - temporarily clears `_external` while the real external body runs
   - proxies and binds the result
   - emits result hooks

That temporary `_external.apply_with(None)` is how the system distinguishes
"running external code now" from "running internal code inside retrace".

### Callback flow

If external code calls back into a Python override on a patched subtype:

1. The override wrapper enters `_override_handler`.
2. While retrace is active, true ext-to-int callbacks route through
   `int_executor()`.
3. `int_executor()`:
   - emits callback hooks
   - restores the external executor around nested outbound calls
   - converts values back to internal form
   - records callback result or callback error

### Allocation flow

When a patched object is allocated during retrace:

- In the normal internal phase, `System._on_alloc` binds the concrete object.
- In the callback/out-of-sandbox phase, `_on_alloc` first triggers
  `async_new_patched` so replay can learn that a new stub must exist, then binds
  the instance.

In recorder mode, `async_new_patched()` writes a callback-like event that
creates a stub object on replay and then binds the concrete instance.

## What Happens During Replay

`io.replayer()` constructs a pipeline that reads raw tape data, resolves binder
handles, and then feeds protocol messages into a replay `System`.

### Source stack

Replay input is layered:

1. `_RawTapeSource`
   Normalizes raw tape objects, including older bind marker shapes.
2. `_ThreadDemuxSource`
   Reads `THREAD_SWITCH` markers and delivers only the current thread's events.
3. `_ReplayBindingState`
   Tracks bind-open and bind-close markers, resolves `stream.Binding` handles
   back to live objects, and exposes `bind()` for replay-time object creation.
4. `_IoMessageSource`
   Decodes tags like `CALLBACK`, `RESULT`, and `CHECKPOINT` into message objects.

This layering is what lets replay preserve both object identity and per-thread
message ordering.

### External call flow

In replay mode, the patched method still enters `_ext_handler`, but the
configured `ext_execute()` does not call the real external function. It reads
forward until it finds the next matching `ResultMessage` or `ErrorMessage`.

While doing that, replay may have to process interleaved callback messages
first, because the recorded external call may have invoked Python callbacks
before producing its final result.

### Callback flow

When replay sees a `CallbackMessage`, it immediately executes the recorded
callback target in the live process. That callback runs through the same patched
Python code path as it did during record. Replay then expects the corresponding
`CallbackResultMessage` or `CallbackErrorMessage`.

### Materialization flow

Replay has extra logic for recorded results that refer to newly materialized
objects.

`on_materialized_result()` and `on_materialized_error()` watch for replay-side
allocations that correspond to recorded external results. If the recorded result
is a `stream.Binding`, replay associates that binding handle with the live
object it just created, then compares the normalized materialized value against
the recorded one.

This is one of the most delicate areas in the design: replay must recreate a
usable live object without accidentally re-entering the proxy boundary in the
wrong phase.

## Materialized Replay

Materialized replay is not the normal replay path for object-returning calls.

The normal rule is still:

1. do not execute the live external callable during replay
2. read the recorded `RESULT` or `ERROR`
3. return or raise the recorded outcome

`replay_materialize` exists only for an unusual edge case: during replay, a
proxied callable may sometimes be invoked while retrace is temporarily disabled.
In that situation the usual replay machinery is not actively driving the call,
but later replay still expects object identity and recorded results to stay
aligned.

The regression this protects is the disabled-call case covered by
`tests/proxy/test_replay_materialize.py`: a replay-disabled call to something
like `_thread.allocate_lock()` must not accidentally consume the next recorded
result that belongs to a subsequent real replayed call.

### What problem it solves

When retrace is disabled around a call:

- the call may execute locally instead of through the normal external replay
  executor
- the returned object may still need to participate in later replayed method
  calls
- replay must not advance the message stream as if that disabled call had been a
  normal replayed external call

So the purpose of materialized replay is narrow: preserve replay alignment when
a live object gets created through a proxied callable in a replay-disabled path.

It is not a statement that "constructors generally run live on replay."

### How a callable gets marked

The install layer exposes a `replay_materialize` directive in module specs.
During patching, `install/patcher.py` adds the current callable identity to
`system.replay_materialize`.

That detail matters because the live module slot may already point at a patched
wrapper rather than the original builtin. The registry therefore tracks the
post-patch callable object that replay will actually see in the disabled path.

Examples currently registered in `src/retracesoftware/modules/stdlib.toml`
include:

- `_thread.allocate_lock`
- `_thread.RLock`
- `ssl.MemoryBIO`
- `random.Random`

### Why binding still matters

Even in this narrow case, the key replay artifact is usually the binding
association between:

- the recorded binding handle
- the live replay object created in the disabled path

When the recorded result is a `stream.Binding`, replay associates that handle
with the live object so later recorded references resolve to the same object.
That keeps subsequent replayed method calls aligned with the object created
outside the usual replay executor path.

### Error handling

Materialized replay also has a paired error path.

If the local disabled-path construction raises, `on_materialized_error()`
consumes the corresponding recorded error and verifies that replay is still
aligned on exception type and message.

### Design constraints

Because this is an escape hatch for a disabled replay path, the bar for using it
is high:

- the callable must be safe to run locally during replay
- the resulting object must be suitable for later replayed calls
- the call must not steal or misalign the next recorded result

This is why `replay_materialize` is an explicit opt-in rather than a general
policy for object-returning external calls.

## `System.run()`

`System.run()` is the point where the kernel becomes active.

It builds:

- `external_executor` with `ext_executor()`
- `internal_executor` with `int_executor()`

Then it:

1. installs both executors into the thread-local gates
2. builds `thread_wrapper` so child threads inherit the active context
3. fires lifecycle start hooks
4. runs the user function
5. clears the executors and thread wrapper on exit

This is why patched types can exist globally while retrace behavior is only
active during a record or replay session.

## `disable_for()`

`disable_for()` runs a callable with both gates temporarily cleared.

This is used for control-plane work that must not itself be retraced, such as:

- stacktrace bookkeeping
- certain binder or materialization helpers
- unexpected-message and desync handlers

If this helper is used incorrectly, retrace can start recording its own replay
plumbing, which usually causes immediate desynchronization or much worse,
subtle divergence later.

## Threads

The proxy is thread-aware.

- `System` assigns stable logical thread ids.
- `wrap_start_new_thread()` propagates the active retrace context into child
  threads.
- Recorder mode emits `THREAD_SWITCH` markers.
- Replay mode demultiplexes the unified stream back into per-thread delivery.

The contract is not just "same messages eventually"; it is "same messages in the
same per-thread order".

## Checkpoints And Debug Mode

In debug mode, `CALL` and callback messages are replaced or augmented with
structured checkpoints.

The checkpoint path compares replay-time values with recorded values using
`equal()` and optional stacktrace deltas. This is a divergence aid, not the main
correctness mechanism, but it is often the fastest way to locate the first
wrong boundary crossing.

## Important Invariants

- The boundary is semantic, not syntactic. A small passthrough or wrapping
  change can change replay behavior without changing signatures.
- Message order matters. If replay consumes a different message sequence than
  record produced, everything after that point is suspect.
- Bind open/close events are part of correctness, not bookkeeping noise.
- Callback execution is part of the external call contract.
- Replay-side materialization must not accidentally perform real external I/O.
- Control-plane work must stay outside the gates.

## Reading The Code

If you are tracing a bug, start here:

- `System.patch_type()`
  How types become part of the boundary.
- `System.ext_executor()` and `System.int_executor()`
  How calls are transformed and observed.
- `System.run()`
  How the gates become active.
- `io.recorder()`
  How record-time hooks emit messages and bindings.
- `io.replayer()`
  How replay consumes messages, runs callbacks, and materializes objects.

If behavior differs only under replay, inspect the first checkpoint or message
misalignment rather than later failures.
