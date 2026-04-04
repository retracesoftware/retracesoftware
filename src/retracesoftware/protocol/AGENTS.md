# Protocol Layer

This directory owns the semantic replay protocol layered above stream transport.
If stream decides how bytes/objects/bindings move, protocol decides what those
messages mean: result, error, async callback, checkpoint, monitor event, and
async materialization.

## Current Core Files

- `__init__.py`
  Declares the stream-vs-protocol split and re-exports the public protocol API.
- `messages.py`
  Semantic message types such as `ResultMessage`, `ErrorMessage`,
  `CallMessage`, `CheckpointMessage`, `MonitorMessage`,
  `AsyncNewPatchedMessage`, and `HandleMessage`.
- `record.py`
  Adapts a stream writer into semantic protocol operations.
- `replay.py`
  Parses protocol messages during replay and manages replay-side
  async-new-patched materialization.

## Mental Model

- Protocol sits above stream. Stream owns bindings, thread switches, and raw
  transport; protocol owns semantic replay events.
- The protocol reader must preserve semantic parity with the protocol writer.
- `ASYNC_CALL` and `ASYNC_NEW_PATCHED` are not incidental details; they define
  how internal callbacks and replay-side materialization round-trip.
- `MonitorMessage` and `CheckpointMessage` are divergence-detection tools. Their
  ordering and visibility are part of the replay contract.
- Replay-side materialization is stateful: `_pending_async_new_patched` is a
  queue, not a convenience cache.
- The higher-level replay/control protocol exposed by `replay_protocol.py` and
  `control_runtime.py` is also a contract: event ordering, `message_index`,
  `breakpoint_hit`, stop reasons, and `set_backstop` semantics are externally
  visible behavior.

## Materialization Invariants

- `AsyncNewPatchedMessage` must be remembered and matched back into later
  results in the same logical order the recording emitted it.
- Matching can happen by object identity or by signature. Changing that matching
  policy can silently break replay for wrapped objects.
- Legacy proxy-stub handling lives in protocol semantics here, not only proxy logic.
- `type_deserializer`, `stub_factory`, `bind(obj)`, and `mark_retraced(obj)`
  must stay aligned; they are one materialization pipeline.
- Clearing `_pending_async_new_patched` too early or too late changes replay
  object identity and can produce spurious binding/materialization failures.

## High-Risk Areas

- `next_message()` tag parsing and event ordering.
- `ASYNC_NEW_PATCHED` creation/materialization ordering.
- `MonitorMessage` and `CheckpointMessage` handling during `sync()`,
  `read_result()`, and `checkpoint()`.
- Callback/exception round-tripping across protocol boundaries.
- Differences between in-memory replay and file-backed replay.
- Any change that changes whether a semantic event is skipped, consumed, or
  surfaced to higher layers.
- Control-protocol event ordering and payload shape drift.
- `set_backstop` / `message_index` semantics drift.

## Working Rules

- Keep stream concerns and protocol concerns separate; do not move binding rules
  into protocol code or semantic rules into stream code casually.
- If you change protocol tags or their ordering, review the matching tests and
  the higher-level callers in `proxy`, `replay_protocol.py`, and
  `control_runtime.py`.
- If you change client-visible control events, reason explicitly about:
  event ordering, stop reasons, `message_index`, and whether tests such as
  `tests/test_stdio_replay.py` need updating in the same diff.
- Treat `AsyncNewPatchedMessage` ordering as a no-break contract.
- If a failure smells like `ExpectedBindingCreate`, check stream first; if it
  smells like wrong semantic event ordering or replay-side materialization
  order, check protocol first.
- Add focused tests when changing message meaning, callback round-tripping, or
  materialization order.

## References

- `src/retracesoftware/protocol/__init__.py`
- `src/retracesoftware/protocol/messages.py`
- `src/retracesoftware/protocol/record.py`
- `src/retracesoftware/protocol/replay.py`
- `src/retracesoftware/replay_protocol.py`
- `src/retracesoftware/control_runtime.py`
