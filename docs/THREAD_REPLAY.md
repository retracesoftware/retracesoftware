# Thread-Aware Record/Replay

Retrace records a single ordered stream for each process. Multi-threaded
programs still produce one trace stream, so Retrace writes thread-switch
control markers whenever recorded boundary traffic moves to another logical
thread. During replay, the reader demultiplexes that stream and each replay
thread blocks until the next recorded message for its logical thread is
available.

That recorded order is the synchronization mechanism. Replay should not rely on
live lock timing, scheduler timing, socket timing, or OS thread ids to decide
what happens next.

## Two Thread Id Layers

There are two related thread-id mechanisms:

- The top-level record path in `src/retracesoftware/__main__.py` creates a
  `src/retracesoftware/threadid/ThreadId` and passes its getter to
  `src/retracesoftware/tape.py` / the stream writer. This preserves low-level
  stream thread context.
- The proxy `System` in `src/retracesoftware/proxy/system.py` owns the logical
  ids used for replay message routing. The main replay context gets an initial
  integer id. Wrapped thread-start functions assign deterministic child ids and
  call `sync()` when the child starts.

The reader stack still understands both legacy `stream.ThreadSwitch` objects
and current protocol-level `"THREAD_SWITCH"` markers. New debugging should focus
on the protocol markers emitted by the proxy recorder.

## Recording Path

At record time:

1. `src/retracesoftware/__main__.py` creates the tape writer with a thread
   getter.
2. `src/retracesoftware/install/` patches configured runtime and library
   surfaces.
3. `src/retracesoftware/proxy/system.py` wraps thread-start APIs so new Python
   threads inherit deterministic logical ids.
4. `src/retracesoftware/proxy/io.py` creates the recorder writer. Its
   `write_thread_switch` helper emits a `"THREAD_SWITCH"` marker before the
   next message when the current logical thread changes.
5. Boundary calls write results, errors, bindings, and sync/checkpoint messages
   to the single process stream.

The important invariant is that every recorded external result is associated
with the logical thread that observed it during record.

## Replay Path

At replay time:

1. A `.retrace` recording is extracted into per-process PidFiles such as
   `recording.d/12345.bin`.
2. Replaying a PidFile launches Python through `python -m retracesoftware
   --recording <PidFile>`.
3. `src/retracesoftware/proxy/io.py` builds a `_ThreadDemuxSource` and an
   `_IoMessageSource`.
4. Each replay thread asks the demux for messages matching
   `system.thread_id()`.
5. If the next message in the trace belongs to another thread, it is buffered
   for that thread. The current thread waits until its own next message is
   available.

This is why replay can make live Python threads appear to follow the same
interleaving as the recording even though the OS scheduler is free to choose a
different order.

## Stream Reader Stack

The file reader pipeline in `src/retracesoftware/stream/reader.py` handles
low-level tape concerns before proxy replay consumes messages:

- `HeartbeatReader` strips heartbeats.
- `WithThreadReader` converts thread-switch controls into `(thread_id, item)`
  tuples. It supports both legacy `ThreadSwitch` controls and newer
  `"THREAD_SWITCH"` markers.
- `PeekableReader` buffers future tuples.
- `DemuxReader` routes tuples by thread id.
- `ResolvingReader` resolves recorded binding objects.
- `ObjectReader` wires those layers together.

The proxy replay path then layers binding state, IO message handling, and
boundary-result validation on top of that stream.

## Synchronization Guarantees

Thread replay depends on three contracts:

- Child threads must receive the same logical ids on record and replay.
- Thread-switch markers must be written before messages for a different logical
  thread.
- The demux must deliver each recorded boundary result to the replay thread that
  originally observed it.

When those contracts hold, normal synchronization primitives can be replayed as
ordinary recorded boundary results. For example, if thread A acquired a lock
before thread B during record, B cannot observe its recorded acquire result
until the demux has delivered A's earlier messages.

## Common Failure Fingerprints

Thread-routing bugs usually show up as one of these symptoms:

- replay timeout with multiple threads blocked in `proxy/io.py`
- `Unexpected message: ... was expecting ...`
- read-past-end errors such as `Could not read: 1 bytes from tracefile`
- a replay thread consuming a result that belongs to another thread
- a background finalizer or shutdown callback trying to read after the trace is
  exhausted

When investigating these, first identify the record/replay point where the
message stream and the current logical thread diverge. Avoid fixing the library
or framework that exposed the bug before confirming the demux/message-order
contract being violated.

## Related Files

| File | Role |
|---|---|
| `src/retracesoftware/__main__.py` | Creates the top-level `ThreadId`, opens tape readers/writers, and starts record/replay. |
| `src/retracesoftware/threadid/__init__.py` | Low-level thread-id helper used by the top-level record path. |
| `src/retracesoftware/proxy/system.py` | Owns proxy logical thread ids and wraps thread-start APIs. |
| `src/retracesoftware/proxy/io.py` | Emits thread-switch markers during record and demuxes replay messages by logical thread. |
| `src/retracesoftware/stream/reader.py` | Builds the file-reader stack that understands thread-switch controls and bindings. |
| `src/retracesoftware/stream/__init__.py` | Exposes stream control types and reader/writer plumbing. |
| `src/retracesoftware/protocol/messages.py` | Defines protocol message types consumed by record/replay. |
| `src/retracesoftware/testing/memorytape.py` | In-memory tape helpers used by tests. |
| `cpp/stream/objectwriter.cpp` | Native stream writer implementation. |
| `cpp/stream/wireformat.h` | Low-level wire format tags and controls. |
