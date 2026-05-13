# Thread-Aware Record/Replay

Retrace records one interleaved stream of events from all application threads.
Each write is tagged with the current stable retrace-python thread id from
`_thread.get_ident()`. During replay, a single peekable stream is consumed in
recorded order. Thread scheduling is driven by cursor checkpoints rather than
per-thread replay queues.

Replay should not rely on live lock timing, scheduler timing, socket timing, or
OS thread ids to decide what happens next. The recorded stream order is the
replay synchronization mechanism.

## Thread Identity

This branch assumes retrace-python provides deterministic `_thread.get_ident()`
values. Retrace does not assign hierarchical Python-side thread ids anymore.
The same API is used everywhere thread identity is needed:

- record writer routing
- replay scheduler routing
- control-runtime coordinate snapshots
- debugger/control-plane thread reporting

`threading.Thread.start` runs under the disabled gate via module config. That
keeps CPython's bootstrap bookkeeping out of the boundary before the native
thread is created, while still leaving `_thread.start_new_thread` and
`threading._start_new_thread` unpatched.

## Recording Path

`stream.writer` is created with `thread=_thread.get_ident`. The native writer
emits a `THREAD_SWITCH` marker when the writing thread changes, followed by the
stable thread id. Record-side `retrace` eval-loop callbacks can also emit
`THREAD_START, <thread-id>`, `THREAD_YIELD, <cursor-delta>`, and
`THREAD_RESUME, <thread-id>` scheduling telemetry.

Those routing messages are transport metadata. They are not user-visible
application events.

## Replay Path

Replay reads the same interleaved stream globally. `THREAD_START` names a newly
started thread before it has a Python cursor. If replay sees a future start
before the child start callback runs, it records that id as pending so the
child can claim it later without parking inside Python thread bootstrap.
`THREAD_YIELD` updates the current scheduled thread's cursor from its delta and
arms `retrace.call_at(thread_id, cursor, callback)` for yielded cursors.
`THREAD_RESUME` names the scheduled thread id; replay only performs a native
handoff when that target has a yielded cursor.

The checkpoint callback consumes the scheduler event and then arms the next
scheduler cursor. Protocol messages are consumed directly from the global
stream once the recorded thread reaches the matching cursor.

Replay uses `retrace.ThreadHandoff` as the native parking primitive at yielded
cursors: `handoff.to(thread_id)` transfers execution to the recorded target
thread and parks the current one until the next transfer.

## Synchronization

The recorded stream order is the replay synchronization mechanism. If one
thread observed a boundary result before another during record, replay
preserves that order by only allowing threads to continue at the cursor points
recorded by the scheduler.

## Common Failure Fingerprints

Thread-routing bugs usually show up as one of these symptoms:

- replay scheduler timeout with multiple threads parked in `proxy/io.py`
- `Unexpected message: ... was expecting ...`
- read-past-end errors such as `Could not read: 1 bytes from tracefile`
- a replay thread consuming a result that belongs to another thread
- a background finalizer or shutdown callback trying to read after the trace is
  exhausted

When investigating these, first identify where the message stream, scheduler
cursor, and current logical thread diverge. Avoid fixing the library or
framework that exposed the bug before confirming the message-order contract
being violated.

## Related Files

| File | Role |
| --- | --- |
| `src/retracesoftware/__main__.py` | Creates the record writer with `_thread.get_ident`. |
| `src/retracesoftware/proxy/system.py` | Enables retrace on child threads without assigning custom ids. |
| `src/retracesoftware/proxy/io.py` | Writes protocol-level thread routing and replays `retrace` scheduler checkpoints. |
| `src/retracesoftware/proxy/messagestream.py` | Decodes, binds, buffers, and schedules replay messages. |
| `src/retracesoftware/stream/reader.py` | Lower-level reader helpers for thread-tagged streams. |
| `retrace.coordinates()` | Runtime cursor snapshots use retrace-python coordinates and `_thread.get_ident()`. |
