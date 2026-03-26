# Proxy Layer

This directory implements the record/replay boundary. Code here decides what
crosses between deterministic "internal" code and nondeterministic "external"
code, what gets written to the trace, what gets replayed from the trace, and
how divergence is detected. Small changes here can silently corrupt replay.

## Current Core Files

- `system.py`
  Current gate-based kernel. Prefer this mental model first.
- `record.py`
  Record-mode behavior. Real external calls execute and emit tape events.
- `replay.py`
  Replay-mode behavior. External calls do not execute; recorded results are fed
  back through the same boundary.
- `messagestream.py`
  Linear tagged message stream reader/writer logic and in-memory replay path.
- `thread.py`
  In-memory thread-switch helpers and per-thread message delivery.
- `protocol.py`
  Writer/reader contracts expected by `System.record_context` and
  `System.replay_context`.

Some older helper modules still exist in this directory. When in doubt, follow
the `System`-based implementation used by the current top-level runtime.

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

## Record And Replay

- `record_context(writer)` executes the real external call and records the
  outcome.
- `replay_context(reader)` does not execute the external call; it reads the
  recorded outcome and returns it directly.
- `normalize` + `checkpoint` are a divergence guard rail. They help locate the
  first mismatch but are not the entire correctness mechanism.
- Weakref callback markers (`ON_WEAKREF_CALLBACK_START/END`) and monitor events
  are real tape events. Treat them as replay-significant, not incidental logging.
- Replay-side stub/materialization paths must not accidentally re-enter the
  active gates while reconstructing proxyable objects.
- File-backed replay and in-memory replay share the same semantics but use
  different plumbing. Keep both paths in mind when changing thread or message logic.
- Proxyable external objects may be stored as `StubRef` metadata and recreated
  through replay-side materialization. Do not "simplify" this without tracing
  the object-identity consequences.
- Wrapped arguments seen inside external method bodies are part of the contract.
  Changes to wrapping/materialization that alter what method bodies observe can
  break replay even if the call signatures still look correct.
- Passthrough predicates for immutable or already-safe values are contract
  behavior, not just optimization. Changing passthrough semantics can break
  callback routing and materialization expectations for patched types.

## Message Stream Invariants

- The message stream ordering matters. Do not casually change the meaning or
  order of `SYNC`, `CALL`, `RESULT`, `ERROR`, `CHECKPOINT`, or `MONITOR`.
- Replay depends on message alignment. If replay consumes a different sequence
  of messages than record produced, everything after that point is wrong.
- Thread-aware recordings depend on correct thread-switch handling and per-thread
  delivery. Be careful with any change that affects thread ids, sync points, or
  callback ordering.
- Side-channel handle messages such as `HandleMessage('STACKTRACE', delta)` are
  intentionally skipped by replay readers in many paths. Do not change that
  behavior casually.
- `MessageStream.result()` intentionally strips traceback/context metadata when
  replay re-raises recorded exceptions so retrace internals do not stay alive
  longer than they did during recording.

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

## Working Rules

- Prefer the current `System` path and verify real call sites from
  `src/retracesoftware/__main__.py` and `src/retracesoftware/install/`.
- If a fix can live in `src/retracesoftware/modules/*.toml` or the install
  layer, prefer that over rewriting boundary logic.
- If you change `system.py`, `record.py`, `replay.py`, `messagestream.py`, or
  `thread.py`, explicitly call out the determinism impact.
- If you change wrapped-argument behavior, passthrough rules, or stub
  materialization, re-check the contract against binding/materialization tests
  rather than only looking at the immediate call site.
- Control-plane work such as debugger I/O, trace reads, or monitoring-related
  plumbing must remain invisible to replay.
- If you touch exception replay or message consumption in `messagestream.py`,
  re-check object-lifetime and weakref/finalizer behavior, not just the happy path.
- Add or update focused tests in the narrowest responsible layer.
- When debugging a replay failure, find the first divergence or misalignment
  instead of patching later symptoms.

## References

- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/proxy/record.py`
- `src/retracesoftware/proxy/replay.py`
- `src/retracesoftware/proxy/messagestream.py`
- `src/retracesoftware/proxy/thread.py`
- `src/retracesoftware/proxy/protocol.py`
- `docs/THREAD_REPLAY.md`
- `docs/DEBUGGING.md`
