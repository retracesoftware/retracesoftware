# Retrace Stream Architecture

This document explains how the `stream` subsystem works today: what it
does, which pieces own which responsibilities, and how data moves from a
live Python process into a trace file and back out again during replay.

The stream layer is the I/O backend for retrace. It knows how to:

- serialize Python values into the retrace wire format
- preserve object identity for bound objects and handle references
- multiplex events from multiple threads into one trace
- write traces asynchronously without blocking Python too aggressively
- read those traces back into replay messages and values

It does **not** decide what should be recorded. That policy lives in the
proxy/install layers. The stream layer only provides the transport and
encoding.

## High-level shape

The stream subsystem has five major parts:

1. Python-facing wrapper module in `src/retracesoftware/stream/__init__.py`
2. Native extension entrypoints in `cpp/stream/module.cpp`
3. Producer-side recording logic in `cpp/stream/objectwriter.cpp`
4. Queue transport and protocol boundary in `cpp/stream/queue.h` and `cpp/stream/queue.cpp`
5. Consumer-side backends and replay reader in `cpp/stream/persister.cpp` and `cpp/stream/objectstream.cpp`

The design is intentionally split into:

- producer side: `ObjectWriter`
- transport boundary: `Queue`
- consumer side: `Persister` or `DebugPersister`
- wire-format encoder/decoder: `MessageStream` and `ObjectStream`

## Python entrypoint

`src/retracesoftware/stream/__init__.py` selects either the release or
debug native extension at import time:

- `_retracesoftware_stream_release`
- `_retracesoftware_stream_debug`

It then exposes high-level helpers and convenience wrappers, especially:

- `writer(...)`: the normal recording entrypoint
- `FramedWriter`: PID-framed output writer
- `Persister`: native async recording backend
- `DebugPersister`: native debug-event backend
- `ObjectStream`: replay-side reader

Typical recording setup is:

1. Python creates a `FramedWriter`
2. Python creates a `Persister`
3. Python creates an `ObjectWriter`
4. `ObjectWriter` asks the persister to create a `Queue`
5. proxy/install code calls into the writer to emit messages

## Native module layout

`cpp/stream/module.cpp` exposes the stream extension types. The important
public native types are:

- `FramedWriter`
- `ObjectWriter`
- `ObjectStream`
- `Persister`
- `DebugPersister`

Some helper types are hidden because they are implementation details:

- `StreamHandle`
- `Deleter`
- `WeakRefCallback`

## Recording pipeline

### 1. `ObjectWriter`: producer-side serializer

`cpp/stream/objectwriter.cpp` is the producer-side object graph walker.
Its job is to turn live Python objects and retrace events into semantic
queue operations.

It is responsible for:

- deciding how a Python value should be represented
- flattening lists, tuples, and dicts into stream structure events
- using direct native encoding for common scalar types
- falling back to the configured serializer or pickle
- separating handle refs, bound refs, and one-shot external wrapped values
- emitting bind/new-patched/delete/thread/heartbeat/flush events
- installing weakref callbacks for delete tracking where needed

It is *not* responsible for low-level queue word encoding anymore. It
only calls semantic queue methods like:

- `push_obj(...)`
- `push_bind(...)`
- `push_new_handle(...)`
- `push_handle_ref(...)`
- `push_bound_ref(...)`
- `push_ext_wrapped(...)`
- `push_delete(...)`
- `push_thread()`

That is a key architectural boundary: the writer emits semantic events,
not raw queue protocol entries.

### 2. `Queue`: transport and semantic protocol boundary

`cpp/stream/queue.h` and `cpp/stream/queue.cpp` now sit in the middle of
the system.

`Queue` owns:

- the forward SPSC queue from producer to consumer
- the return SPSC queue used to release consumed Python object refs
- inflight byte accounting and backpressure waiting
- the internal queue protocol representation (`QEntry`)
- producer-side payload ref handoff
- consumer-side decode and post-callback owned-payload release

The queue event taxonomy is now:

- owned Python payload: queue owns a `Py_INCREF` and returns or decrefs it after consumption
- bind: opaque object identity token with no queue ownership or size accounting
- handle ref: explicit identity for `StreamHandle`
- handle delete: infrequent handle-destruction event carried as a command payload
- delete: opaque object identity token with no queue ownership or size accounting
- bound ref / bound-ref delete: identity for already-bound patched or internal wrapped objects
- new external wrapped: a one-shot wrapped value that is never referred to again by identity
- command/control entries: list/tuple/dict headers, handle-delete, new-patched, thread, heartbeat, flush, shutdown, serialize-error

This is the most important abstraction in the current design.

### Why `Queue` matters

Before the recent refactors, both `ObjectWriter` and `persister.cpp`
understood the low-level tagged queue representation. Now:

- producers talk to `Queue` through semantic push methods
- consumers talk to `Queue` through `QueueConsumer`
- only `Queue` knows how semantic events are represented internally

That means the internal queue protocol can change without forcing
parallel edits in `ObjectWriter` and the persisters.

### Producer-side responsibilities in `Queue`

On the producer side, `Queue` now handles:

- blocking or timing out when the queue is full
- inflight reservation for object payloads
- `Py_INCREF` when an owned object is handed to the queue
- rollback if enqueue fails

So when `ObjectWriter` does `queue->push_obj(obj, size)`, the queue owns
the reference-management details.

### Pointer encoding and escape path

`Queue` uses compact tagged pointer words when the pointer is safely
8-byte aligned. That gives it enough inline space to distinguish:

- owned object payloads
- bind events
- handle refs
- bound refs and bound-ref deletes
- raw pointer payload words used inside compound commands

When bit `0x4` is already set in the pointer value, `Queue` falls back to
an escaped-pointer command followed by a raw payload word. This matters
most for static `PyTypeObject*` values on 32-bit builds, which may only
be 4-byte aligned.

### Consumer-side responsibilities in `Queue`

On the consumer side, `Queue` exposes:

- `try_consume(QueueConsumer&)`
- `consume(QueueConsumer&)`

`Queue` decodes the internal protocol and invokes semantic callbacks such
as:

- `consume_object`
- `consume_bind`
- `consume_new_handle`
- `consume_thread`
- `consume_list`
- `consume_dict`
- `consume_shutdown`

After the callback returns, `Queue` handles cleanup only for owned payloads:

- normal consumers: payload is placed on the return queue
- draining consumers: payload is released immediately

Identity-only events such as `bind`, `delete`, handle refs, and bound refs
never go through the return queue.

## The internal queue protocol

The internal queue protocol is defined in `cpp/stream/queueentry.h`.

It is a tagged word protocol:

- `PTR_OWNED_OBJECT`
- `PTR_BIND`
- `PTR_HANDLE_REF`
- `PTR_BOUND_REF`
- `PTR_BOUND_REF_DELETE`
- `PTR_RAW_POINTER`
- `TAG_COMMAND`

Commands include:

- `CMD_FLUSH`
- `CMD_SHUTDOWN`
- `CMD_LIST`
- `CMD_TUPLE`
- `CMD_DICT`
- `CMD_HEARTBEAT`
- `CMD_DELETE`
- `CMD_THREAD`
- `CMD_PICKLED`
- `CMD_NEW_HANDLE`
- `CMD_NEW_PATCHED`
- `CMD_SERIALIZE_ERROR`

This protocol is now intentionally treated as an internal transport
format, not the public interface between the writer and persister.

## Consumer backends

`cpp/stream/persister.cpp` provides two consumer backends.

### `Persister`

This is the normal recording backend.

It owns:

- a `Queue`
- a background writer thread
- a background return-drain thread
- a `MessageStream`
- thread-switch cache state
- `Ref -> wire handle index` mapping

Its `WriterConsumer` implementation receives semantic queue events and
translates them into `MessageStream` calls:

- `consume_object` -> `stream->write(obj)`
- `consume_bind` -> `stream->bind(reinterpret_cast<PyObject*>(ref))`
- `consume_new_handle` -> `stream->write_new_handle(obj)`
- `consume_thread_switch` -> `stream->write_thread_switch(handle)`
- structure callbacks recurse through nested values

The return thread drains queued Python objects after they have been fully
consumed, updates inflight accounting, and decrefs them outside the hot
path of serialization.

### `DebugPersister`

This is a debugging backend for inspecting the queue protocol at a higher
level.

Instead of writing wire-format bytes, it:

- consumes semantic queue events
- builds Python event tuples
- calls a Python handler or `handler.handle_event(event)`

This is useful when debugging the recording pipeline itself.

Because it sits on the semantic consumer boundary, it can observe the
stream pipeline without caring about `QEntry` internals directly.

## Wire-format writer

`cpp/stream/writer.h` defines `MessageStream`, which converts semantic
stream events into the actual retrace binary format written to disk.

`MessageStream` owns wire-format concerns such as:

- compact integer/string/bytes encodings
- writing bound-type references
- writing handle refs and handle-delete records
- writing pre-pickled payloads
- writing thread-switch records
- interning some repeated values

This layer is below `ObjectWriter` and above `FramedWriter`.

Conceptually:

- `ObjectWriter` decides *what* event/value to emit
- `Queue` transports semantic events across threads
- `MessageStream` decides *how* those events become bytes

## Framed output

`FramedWriter` is the outer transport to disk or pipe. In the normal
path it writes PID-framed chunks so multiple processes can share one
trace.

The Python wrapper also writes a JSON preamble with process metadata at
the beginning of a trace file.

So the on-disk structure is roughly:

1. optional text preamble / process info
2. PID-framed binary stream payloads
3. inside each frame: `MessageStream` wire-format messages

## Replay pipeline

Replay uses `cpp/stream/objectstream.cpp`.

`ObjectStream` is the inverse of the recording side. It reads a trace
file and reconstructs replay messages and values for the proxy layer.

It owns replay-side state such as:

- current file position
- handle table
- filename table
- interned strings
- binding table
- pending bind state

It also receives Python callbacks/factories for special replay events:

- deserialize pickled payloads
- create stack delta objects
- create thread switch messages
- create dropped/heartbeat messages
- create stubs for unresolved bound objects

So:

- `Persister` + `MessageStream` write the wire format
- `ObjectStream` reads the same wire format back into replay objects

## Object identity and handles

One of the trickiest responsibilities of the stream layer is preserving
object identity.

There are two related mechanisms:

### Bindings

Bindings are used for stable type/object identity in the message layer.
They let replay refer back to previously known objects rather than
serializing them from scratch every time.

### Handles / refs

The current stream path uses `Ref` as an opaque pointer-shaped token
(`void*` in `queueentry.h`).

Important points:

- producer-side code emits `Ref` values
- consumer-side code maps `Ref` to wire handle indices
- deletes are emitted as `Ref` deletes
- `StreamHandle` carries a `Ref`, not an integer index

This split is deliberate: producer identity does not have to match the
wire-format numbering scheme.

## Threading model

Recording is intentionally multi-thread aware.

Important thread-related behaviors:

- `ObjectWriter` can stamp thread changes into the queue
- `Persister` resolves `PyThreadState*` to the writer’s thread handle
- `MessageStream` emits thread-switch records
- replay-side `ObjectStream` reconstructs thread switch events

The queue also uses a return-drain thread so Python object decrefs do not
have to happen synchronously on the writer thread.

## Error handling

The stream layer distinguishes a few classes of failure:

- queue stall/backpressure failures on the producer side
- serialization failures during recording
- debug-handler failures in `DebugPersister`
- read timeouts or malformed data during replay

Some failures can be converted into explicit recorded events, such as
`CMD_SERIALIZE_ERROR`, while others are fatal when `quit_on_error` is
enabled.

## Extension points

If you want to change or extend the stream layer, these are the best
places to do it:

### Add a new semantic recording event

Usually requires edits in:

- `queueentry.h` for a new internal command, if needed
- `Queue` push/consume methods
- `ObjectWriter` to emit it
- `Persister::WriterConsumer` to handle it
- `DebugPersister::EventConsumer` to expose it
- `MessageStream` and `ObjectStream` if it affects the wire format

### Change queue transport details

Prefer changing only `Queue` and `queueentry.h`.

The current architecture is designed so:

- `ObjectWriter` should not need to know the transport encoding
- persisters should not need to decode raw queue words

### Change wire-format encoding

Prefer changing:

- `MessageStream` in `writer.h`
- `ObjectStream` in `objectstream.cpp`

without changing `ObjectWriter` or `Queue` unless the semantic model
also changes.

## How stream is coupled to proxy

Formally, `proxy` depends only on the abstract `Writer` and `Reader`
protocols in `src/retracesoftware/proxy/protocol.py`, not on the stream
implementation directly. That part is true.

In practice, though, the stream subsystem is fairly tightly coupled to
how `proxy` works, because the native stream implementation is the main
backend that actually satisfies those protocols.

The most useful way to think about the coupling is:

- `proxy` defines the event model
- `stream` is the concrete transport and encoding for that event model

So even when the import dependency is abstract, the behavioral contract
is shared.

### The writer-side contract

During recording, `System.record_context()` and related proxy logic
expect a writer that can express a very specific sequence of events:

- `bind`
- `write_call`
- `sync`
- `write_result`
- `write_passthrough_result`
- `write_error`
- `checkpoint`
- `stacktrace`

Those are not arbitrary. They reflect the structure of proxy’s record
model:

- sandbox objects crossing into retraced code
- external results crossing back out
- ext→int callbacks
- thread synchronization points
- divergence-check checkpoints
- stack trace annotations

The stream writer exists to faithfully encode that model.

### The reader-side contract

On replay, `System.replay_context()` expects a reader that can:

- resynchronize the current thread with `sync()`
- return the next recorded result with `read_result()`
- surface recorded errors as replay exceptions
- reintroduce binds at the correct time
- compare checkpoints when normalization is enabled

That means the stream replay side is coupled not just to values, but to
proxy’s control flow expectations.

If replay consumes the wrong message kind at the wrong time, the failure
is not just a malformed byte stream. It is a proxy-level semantic
misalignment.

### Identity semantics are shared

The stream layer has its own native machinery for bindings and handles,
but the *reason* those exist comes from proxy semantics.

Proxy cares about:

- when a patched object first enters the sandbox
- when external code allocates an object that now needs identity
- when later messages should refer back to the same logical object
- when an object should be considered freed

Stream implements those rules with:

- bind messages
- new-patched / new-handle messages
- `Ref` tokens and wire handle indices
- delete messages

So object identity is not purely a stream concern and not purely a proxy
concern. The meaning is shared across both layers.

### Threading semantics are shared

The stream layer’s thread switch and sync machinery is also tightly tied
to proxy behavior.

Proxy needs:

- a way to interleave many threads into one recording
- a way for replay to recover “the next message for this thread”
- a way to make ext calls return to the correct logical thread

Stream implements that with:

- writer-side thread stamps
- persister-side thread-handle switching
- replay-side thread-switch message reconstruction
- `sync()` markers in the protocol contract

So this is another area where the coupling is semantic, not just
incidental.

### Checkpoints are a proxy concept carried by stream

Checkpointing is a good example of the relationship.

The concept originates in proxy:

- normalize a value at a call boundary
- store it during record
- compare it during replay

But the bytes that carry checkpoint information live in the stream
format, and replay-side enforcement depends on the stream reader
returning the right semantic objects at the right time.

So checkpointing is proxy policy implemented by stream transport.

### Stack traces are similarly coupled

Stack traces are optional metadata in the protocol, but they are still
coupled to proxy call boundaries.

Proxy decides *when* stack traces matter:

- around external calls
- around callback transitions

Stream decides *how* they are captured and encoded:

- stack delta representation
- serialization format
- replay reconstruction callback

Again, the ownership is split, but the semantics are shared.

### Why the dependency still matters

It is still valuable that `proxy` does not import `stream` directly.
That buys:

- testability with alternate in-memory backends
- a clean abstract `Writer` / `Reader` seam
- less compile/runtime coupling across packages

But that abstraction should not be mistaken for full independence.

The stream backend is effectively the canonical native implementation of
the proxy protocol, so the two layers co-evolve. A change in proxy’s
event model often requires a corresponding stream change, even when the
Python import graph remains one-way.

### Proxy types, patches, wrapped values, and binding

Another important source of coupling is that stream is not just moving
plain Python values around. It is moving values that have already passed
through proxy’s patching and wrapping model.

Those concepts come from proxy, but stream has to preserve their meaning.

#### Patched types

In proxy, `System.patch_type(cls)` changes a type in place so calls on
that type are routed through the gate machinery in
`src/retracesoftware/proxy/system.py`.

That gives proxy the ability to intercept:

- int→ext calls on patched base/C types
- ext→int callbacks through Python overrides
- allocation/binding lifecycle events for patched objects

From stream’s perspective, a “patched object” is not just a normal Python
object. It is an object whose identity and lifecycle now matter to the
record/replay protocol.

That is why stream has special machinery for:

- `bind`
- `new_patched`
- handle/ref creation
- delete tracking

So patching starts in proxy, but stream must encode the consequences of
that patching.

#### Proxy wrapper types

Proxy also introduces wrapper/proxy classes in
`src/retracesoftware/proxy/proxytype.py`, such as:

- `DynamicProxy`
- `ExtendingProxy`
- `InternalProxy`

These are not just implementation details of proxy. They affect what
crosses the stream boundary.

At a high level:

- proxy wraps objects so later operations can be routed through replay or
  record gates
- stream must avoid serializing the wrapper mechanics as if they were the
  real external value
- replay must reconstruct something with equivalent behavioral meaning

So stream often needs to serialize the underlying target identity or a
stub description rather than the literal wrapper instance.

#### External wrapped vs internal wrapped

Although the exact type names live mostly in `utils` and proxy code, the
important distinction is conceptual:

- **external wrapped** values represent outside-world objects that have
  been wrapped so internal code can interact with them through the proxy
  system
- **internal wrapped** values represent internal/sandbox objects crossing
  outward in a form the proxy machinery can later re-associate with their
  internal identity

That distinction matters to stream because the same-looking Python object
may mean very different things depending on which side of the boundary it
came from.

Some practical consequences:

- stream serialization cannot blindly pickle wrapped values and call it
  done
- some wrapped values are converted to symbolic/stub references instead
  of full object payloads
- wrapped external objects may need stable identity tracking instead of
  value serialization

You can see one direct sign of this in `src/retracesoftware/stream/__init__.py`,
where wrapped objects are serialized as `StubRef(...)` metadata rather
than ordinary value payloads.

#### Why stubs exist

On replay, stream often cannot or should not recreate the original live
external object. Instead it reconstructs an object that preserves the
proxy-facing interface.

That is the role of stub types and stub refs:

- record side stores enough type/interface metadata
- replay side uses a stub factory to materialize a lightweight stand-in
- proxy then wraps that stand-in so later method calls route back through
  replay

So stubs are another coupling point:

- proxy defines why they are needed
- stream defines how their metadata is recorded and replayed

#### Binding is where the models meet

Binding is the clearest place where proxy and stream semantics meet.

In proxy, binding means roughly:

- “this object/type has entered the protocol”
- “future references should preserve identity rather than treat this as a
  fresh unrelated value”

In stream, binding becomes concrete messages and state:

- `ObjectWriter.bind(...)`
- `Queue::push_bind(...)`
- `Persister::WriterConsumer::consume_bind(...)`
- `MessageStream::bind(...)`
- replay-side bind handling in `ObjectStream`

This is why binding is more than “bookkeeping.” It is the bridge between
proxy’s object-identity semantics and stream’s serialized representation.

#### `new_patched` vs `bind`

These two are related but not identical.

- `bind` says an existing object/type must now participate in the stream
  identity model
- `new_patched` says a patched object was allocated and needs to be
  introduced as a fresh protocol identity

That distinction matters to replay because it affects whether the replay
side should:

- attach to an already-known identity
- or create/register a new one

Again, proxy defines the lifecycle meaning; stream must preserve it.

#### Handle refs are the transport-level identity bridge

The stream path now uses `Ref` tokens as opaque producer-side identity
markers and maps them to wire indices on the consumer side.

That mechanism is tightly connected to proxy wrapping/binding semantics:

- wrapped or patched objects need stable identity
- repeated observations of “the same logical object” must replay as the
  same logical object
- delete notifications must retire the correct identity

So while refs are a stream mechanism, they are in service of proxy’s
identity rules.

#### Why this is hard to fully decouple

This part of the design is hard to decouple because proxy and stream are
sharing a model of:

- what counts as “the same object”
- when an object becomes protocol-visible
- whether a value should be materialized, stubbed, rebound, or referred
  to by handle
- how boundary-crossing objects should behave on replay

If stream ignored those distinctions, replay would still decode bytes,
but it would no longer reconstruct the same behavioral world that proxy
expects.

#### Practical rule of thumb for these concepts

When changing proxy patching/wrapping logic, ask:

- does this change when objects become bound?
- does this change whether a boundary-crossing object should be treated
  as a value, a stub, or a stable identity?
- does this change whether an object is “internal” or “external” from
  the protocol’s point of view?

If yes, stream documentation and often stream code need to change too.

### Practical rule of thumb

When changing `proxy`, ask:

- does this introduce a new kind of record/replay event?
- does this change bind or identity timing?
- does this affect sync, thread switching, or callback ordering?
- does this change checkpoint or stacktrace semantics?

If the answer is yes, stream probably needs to change too.

When changing `stream`, ask:

- does this preserve the `Writer` / `Reader` behavior expected by proxy?
- does replay still deliver the same semantic event ordering?
- do bind, handle, delete, thread, and checkpoint meanings stay intact?

If the answer is no, the change is not just an internal stream refactor;
it is a protocol change across both layers.

### Good mental model

The cleanest mental model is:

- `proxy` owns the semantics of recording and replay
- `stream` owns the concrete realization of those semantics

That is why they are formally separated, but still practically coupled.

### Proxy protocol to stream mapping

The table below is a practical map from the abstract proxy protocol to
the current stream implementation.

#### Record-side (`proxy.protocol.Writer`)

| Proxy writer hook | Stream entrypoint | Main native path | Notes |
|---|---|---|---|
| `bind(obj)` | `stream.writer.bind()` | `ObjectWriter.bind()` | Records that a patched object/type is now known in the stream identity model. |
| `write_call(...)` | native `ObjectWriter` call-writing path | `ObjectWriter.write_all()` and value emission | Records ext→int callback activity in stream form. |
| `sync()` | native `ObjectWriter.sync()` | queue thread-stamp / sync message path | Gives replay a way to resynchronize the current logical thread. |
| `write_result(value)` | native `ObjectWriter.write_result()` | `push_value()` and queue semantic pushes | Encodes the result value of an external call. |
| `write_passthrough_result(value)` | native `ObjectWriter.write_passthrough_result()` | same serialization path with passthrough semantics | Used when a call should be replayed as a live passthrough result. |
| `write_error(exc)` | native `ObjectWriter.write_error()` | exception serialization path | Records external-call failure so replay can re-raise it. |
| `checkpoint(value)` | native `ObjectWriter.checkpoint()` | stream message/control path | Carries normalized values for divergence detection. |
| `stacktrace()` | native `ObjectWriter.stacktrace()` | stack delta capture + serialization | Records stack metadata at proxy call boundaries. |

#### Replay-side (`proxy.protocol.Reader`)

| Proxy reader hook | Stream entrypoint | Main native path | Notes |
|---|---|---|---|
| `bind(...)` | replay adapter around `ObjectStream` | bind message handling in `ObjectStream` | Reintroduces bound identities into replay at the right time. |
| `sync()` | replay adapter around `ObjectStream` | sync/thread-switch message consumption | Advances until this thread reaches its next replay boundary. |
| `read_result()` | replay adapter around `ObjectStream` | next replay message/value from `ObjectStream` | Returns the recorded result or raises the recorded error. |
| `checkpoint(value)` | replay adapter around `ObjectStream` | checkpoint message comparison path | Compares current normalized replay value against recorded data. |

#### Python wrapper seams

The main Python wrapper points that bridge proxy and stream today are:

- `src/retracesoftware/stream/__init__.py:writer`
- `src/retracesoftware/stream/__init__.py:reader`
- the proxy protocol declarations in `src/retracesoftware/proxy/protocol.py`

Those wrappers are thin, but they are where the abstract proxy contract
is concretely bound to the native stream backend.

## Mental model summary

The easiest way to think about the stream subsystem is:

1. `ObjectWriter` turns Python runtime activity into semantic stream events.
2. `Queue` transports those events across threads and owns queue-local lifetime/backpressure rules.
3. `Persister` turns semantic events into `MessageStream` calls.
4. `MessageStream` encodes those calls into retrace binary bytes.
5. `ObjectStream` decodes those bytes back into replay objects/messages.

That separation is the core design:

- serialization policy in `ObjectWriter`
- transport policy in `Queue`
- backend policy in `persister.cpp`
- wire-format policy in `MessageStream` / `ObjectStream`

When those boundaries stay clean, the stream layer is much easier to
change safely.
