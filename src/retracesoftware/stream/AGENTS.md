# Stream Layer

This directory owns the low-level replay transport contract. It is below the
semantic protocol layer and above the native queue/persister backend. Code here
decides how objects, bindings, heartbeats, and thread-switch markers move
through the tape. Many "protocol" failures are actually stream contract bugs.

## Current Core Files

- `__init__.py`
  Runtime-selectable stream backend, writer construction, binding-aware
  serialization, heartbeat/fork handling, and public reader/writer API.
- `reader.py`
  Replay reader stack: heartbeat stripping, thread demux, binding resolution,
  and the `ExpectedBindingCreate` contract.

## Mental Model

- Stream is transport-level, not business logic. It owns raw object movement,
  binding records, thread-switch markers, and transport framing.
- `BindingCreate`, `BindingLookup`, and `BindingDelete` are part of the replay
  contract. If their creation/consumption order changes, replay can fail long
  before higher-level code notices.
- `ObjectReader.bind(obj)` is intentionally stateful: it must consume the next
  visible `BindingCreate` record at the right moment. `ExpectedBindingCreate`
  means the reader/writer contract has drifted.
- `Heartbeat` and `ThreadSwitch` are control records. They should help transport
  and diagnosis without changing user-visible replay behavior.
- `binary` and `unframed_binary` are different transport formats. Python replay
  currently expects `unframed_binary`.
- File-backed and in-memory replay paths must stay semantically aligned even if
  the plumbing differs.

## Binding And Routing Invariants

- The next visible replay object must not unexpectedly be `BindingCreate`; bind
  records are consumed through `bind(obj)`, not surfaced as ordinary payloads.
- `BindingLookup` resolution must use the current live binding table for the
  correct thread.
- `BindingDelete` records are internal maintenance records; deleting or exposing
  them at the wrong layer changes replay behavior.
- `PeekableReader`, `DemuxReader`, and `ResolvingReader` must preserve parity:
  peeking must not observe a different logical stream than replay consumes.
- Thread demux is part of correctness, not just performance. Wrong routing can
  manifest as binding/materialization failures far away from the root cause.

## High-Risk Areas

- `ExpectedBindingCreate` / `bind(obj)` failures.
- Reader/writer parity for bindings, async-new-patched records, and deletes.
- `ThreadSwitch` handling and current-thread routing.
- Heartbeat skipping and buffering behavior.
- `async_new_patched` transport writes and replay-side replay ordering.
- Changes that alter whether wrapped objects serialize as bindings, stub refs,
  or plain payloads.
- Fork safety, PID framing, and parent/child stream reopening.

## Working Rules

- Treat binding/materialization failures as stream contract issues first.
- If you change `reader.py`, reason through `next()`, `bind()`, `peek()`, and
  delete consumption together; they are one contract.
- If you change writer-side binding emission, update or re-check the matching
  reader-side expectations in the same diff.
- Do not change `BindingCreate` / `BindingLookup` visibility casually.
- If a fix changes transport ordering, call out whether it affects:
  binding creation, thread routing, heartbeat skipping, or fork behavior.
- Prefer adding focused tests in `tests/stream/` when the failure is about
  binding, demux, format, or reader/writer parity.

## References

- `src/retracesoftware/stream/__init__.py`
- `src/retracesoftware/stream/reader.py`
- `tests/stream/test_persister.py`
- `tests/stream/test_stream_smoke.py`
- `tests/stream/test_thread_callback_regression.py`
