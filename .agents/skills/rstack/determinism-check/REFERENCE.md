# Determinism Check — Hazard Examples

Concrete examples for the densest checklist categories. Consult these when a
category in the main checklist is ambiguous for the diff under review.

## Replay message alignment (§3)

**Hazard: extra SYNC emission.** A refactor wraps a previously-inline I/O call
in a helper that itself emits `SYNC`. During replay the extra message shifts
every subsequent message index, causing `UnexpectedMessage` on the next
`RESULT` consumption. Fix: ensure the helper delegates to the existing
boundary call rather than adding its own sync point.

**Hazard: exception traceback extends object lifetime.** A change to
`messagestream.py` catches an exception and stores the traceback. The
traceback holds references to local frames, keeping proxied objects alive past
their expected `BindingDelete`. During replay, the deferred deletion emits a
late message that misaligns the stream. Fix: explicitly clear `__traceback__`
after handling.

## Binding and materialization contract (§4)

**Hazard: early bind on lazily-wrapped argument.** A performance optimization
calls `bind(obj)` at import time rather than at first external method call.
During replay, the `BindingCreate` record appears before the replay stream
expects it, producing `ExpectedBindingCreate`. Fix: defer `bind()` to the
original call site.

**Hazard: passthrough path divergence.** An `isinstance` check is added to
short-circuit immutable values. During recording, the value goes through the
proxy; during replay, it now skips it. The stream sees a `CALL` without a
matching `RESULT`. Fix: apply the short-circuit identically in both modes, or
guard it behind `is_replay()`.

## Contract drift (§9)

**Hazard: extra DAP event before stop.** A logging improvement emits a
`stopped` event with `reason: step` before the real `breakpoint_hit` event.
The VS Code extension sees two stop events and steps twice. Fix: suppress
informational stops when a breakpoint stop is pending in the same message
batch.

**Hazard: flag rename without test update.** A CLI flag is renamed from
`--trace-dir` to `--trace-path`. The integration tests still pass `--trace-dir`,
which is silently ignored, and the tests pass vacuously because the default
path happens to work. Fix: update all test invocations and add a deprecation
warning (or hard error) for the old flag name.
