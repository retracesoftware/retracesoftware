---
name: determinism-check
description: >
  Manually review a proposed change for replay determinism hazards. Use only
  for replay-sensitive edits touching proxy boundary logic, stream or protocol
  semantics, replay message handling, Go replay/control behavior, thread/fork
  behavior, weakref or finalizer timing, module interception coverage, or
  similar control flow. Do not use for general code review, trivial cleanup,
  or ordinary non-replay changes.
---

# Determinism Check

Use this skill before approving or committing a change that could affect replay
correctness. This is a focused review checklist, not a full code review.

Start by reading the relevant local `AGENTS.md` files and the actual diff. In
this repo, the most common high-risk areas are:

- `src/retracesoftware/stream/`
- `src/retracesoftware/protocol/`
- `src/retracesoftware/proxy/`
- `src/retracesoftware/install/`
- `src/retracesoftware/modules/*.toml`
- `src/retracesoftware/dap/`
- `go/replay/`
- `cpp/stream/`
- `cpp/utils/`
- `cpp/cursor/`

If the change is only comments, renames, formatting, or other non-semantic
cleanup, say so explicitly and return `GREEN`.

## Checklist

For each relevant category below, decide whether the change is `safe`,
`hazard`, or `not applicable`.

### 1. Iteration and ordering

- Does replay-sensitive code now iterate over `set`/`frozenset` values?
- Does it assume dict order that could differ if insertion order changes?
- Does it sort or compare using `id()`, `hash()`, object addresses, or `repr()`?

### 2. External nondeterminism

- Does the change add calls to time, random, filesystem, sockets, subprocesses,
  environment access, or other nondeterministic library/OS behavior?
- If yes, is that behavior already intercepted by the boundary or covered by
  `src/retracesoftware/modules/*.toml`?
- If the fix belongs in module interception coverage, say that instead of
  recommending a deeper runtime rewrite.

### 3. Replay message alignment

- Does the change affect when `SYNC`, `CALL`, `RESULT`, `ERROR`,
  `CHECKPOINT`, `MONITOR`, or weakref callback markers are emitted or consumed?
- Could it change per-thread routing, PID switching, or message ordering?
- If `messagestream.py` changed, could exception tracebacks, skipped handle
  messages, or result consumption change object lifetimes or divergence timing?

### 4. Binding and materialization contract

- Does the change alter when `BindingCreate`, `BindingLookup`, or
  `BindingDelete` records are emitted, consumed, or resolved?
- Could it change replay-side materialization order for wrapped objects,
  `StubRef`, or `ASYNC_NEW_PATCHED` values?
- Could it change what external method bodies observe for wrapped arguments, or
  whether immutable/patched values now take a different passthrough path?
- Could `bind(obj)` now happen too early, too late, or on the wrong logical
  object/thread?
- If the symptom is `ExpectedBindingCreate`, have you checked stream/protocol
  contract drift before changing unrelated higher-level code?

### 5. Threading and fork

- Does the change alter thread creation, thread-id routing, locks, wakeups,
  synchronization, or callback ordering?
- Does it affect fork behavior, parent/child replay setup, or trace reopening?
- Could it perturb replay ordering even if the code still "works" functionally?

### 6. Object lifetime and cleanup

- Does it change weakrefs, `__del__`, finalizers, GC timing, or shutdown order?
- Could an exception or traceback now keep retrace internals alive longer than
  during recording?
- Does install-layer lifecycle or `atexit` behavior move I/O inside or outside
  the recorded context?

### 7. Control-plane observer effect

- Does it add debugger I/O, trace reads, monitoring callbacks, logging, or
  other control-plane work in replay-sensitive paths?
- If yes, does it bypass retrace gates so the debugger/tooling does not record
  itself?

### 8. Native/runtime assumptions

- Does the change cross Python-version-specific branches or CPython internals?
- Does it move Python object access into native hot paths, queue loops, or
  GIL-free sections?
- Does it change queue protocol, inflight accounting, thread-switch injection,
  or cursor state assumptions?

### 9. Contract drift

- Does the change alter CLI flags, command names, event ordering, stop reasons,
  response payload shape, or debugger/control-protocol semantics?
- Could it insert extra events ahead of expected `breakpoint_hit` or stop
  events, or change `set_backstop` semantics relative to replay progress and
  `message_index`?
- If tests/helpers still assume old behavior, are they being updated in the
  same diff?
- Could this be test drift or protocol drift rather than a pure determinism bug?

## Output

Return one of:

- `GREEN`: no determinism hazard found, or the change is clearly non-semantic.
- `YELLOW`: plausible hazard or missing evidence. List the risky points and what
  should be checked or tested before merge.
- `RED`: confirmed determinism hazard. Explain the failure mode and say what
  must change before merge.

Keep the result short and concrete:

1. Scope: what files/behavior you checked.
2. Verdict: `GREEN`, `YELLOW`, or `RED`.
3. Findings: only the relevant hazards.
4. Recommended follow-up: specific tests or code paths to verify.
