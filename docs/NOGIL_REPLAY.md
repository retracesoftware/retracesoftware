# No-GIL Replay Notes

Status: exploratory design. This is not an implemented Retrace contract.

This document sketches a possible record/replay strategy for free-threaded
CPython builds where the GIL is disabled. It assumes ordinary Retrace boundary
recording still handles external effects, while additional thread telemetry
helps reproduce schedules that matter to shared-memory behavior.

## Problem

Current threaded replay can lean on a mostly serialized Python execution model:
thread switches are observable scheduling events, and external boundary events
are globally ordered in the trace.

In a no-GIL build, multiple Python threads can execute Python bytecode and C API
operations at the same time. Recording only locks, queues, and external calls is
enough for correctly synchronized programs, but it is not enough to reproduce a
bug caused by an unsynchronized shared-object race.

For example:

```python
# thread A
state["ready"] = True

# thread B
if state.get("ready"):
    fail()
```

If no lock orders the write and read, a trace of only lock operations has no
event that says which access won the race.

## Goals

- Preserve deterministic replay for external effects.
- Preserve strong replay for programs synchronized through locks, queues,
  conditions, events, semaphores, barriers, thread lifecycle operations, and
  futures.
- Record synchronization handoffs as causal edges between thread progress
  points.
- Detect objects that escape their creating thread.
- Classify escaped-object access that occurs outside a known synchronization
  region.
- Support schedule search when the original run contains an unsynchronized
  race.
- Keep normal production overhead low.
- Avoid changing the public `PyObject` layout or requiring a custom wheel ABI.

## Non-Goals

- Do not attempt CPU-level memory replay.
- Do not record every bytecode interleaving.
- Do not promise deterministic replay for arbitrary data races without either
  schedule search or stricter instrumentation.
- Do not add fields to `PyObject`; that would break binary compatibility with
  stock extension wheels.

## CPython No-GIL Background

Free-threaded CPython keeps reference counting, but changes the object header
and refcount paths. A simplified no-GIL object header is:

```c
struct _object {
    uintptr_t ob_tid;          // biased refcount owner, or zero when unowned
    uint16_t ob_flags;
    PyMutex ob_mutex;          // per-object lock
    uint8_t ob_gc_bits;
    uint32_t ob_ref_local;     // owner-thread local refcount
    Py_ssize_t ob_ref_shared;  // shared atomic refcount and state bits
    PyTypeObject *ob_type;
};
```

`ob_tid` is a biased reference-count owner, not a semantic data owner. If the
current thread owns the bias, local refcount updates are cheap. Other threads
can still access the object through shared refcount paths. Objects can also
become unowned or merged.

This makes `ob_tid` useful as a sharing signal, but not as a lock.

## Core Model

No-GIL replay should be split into two phases:

1. Replay deterministic inputs and synchronization handoffs.
2. If replay becomes hard to reproduce, analyze escaped-object access and report
   the unguarded shared state that made the schedule matter.

The trace should contain enough information to make the external world
deterministic. It should not try to capture every unsynchronized memory
observation. Instead, it should identify where shared-memory scheduling matters
and constrain search to those regions.

The primary causal facts should come from synchronization objects, not periodic
global epochs. A lock acquire that follows another thread's unlock is a precise
happens-before edge. A queue get that consumes another thread's put is another.
These edges are cheaper and more useful than asking every thread where it was at
some global moment.

This means a no-GIL replay failure can have two useful outcomes:

- replay succeeds because all relevant cross-thread behavior was synchronized,
- replay reports a specific unguarded shared object access that made exact
  reproduction schedule-sensitive.

The second outcome is not a replay failure in the ordinary sense. It is a race
diagnostic: "this object was read or written across threads without a recorded
synchronization edge."

## Customer Outcome

No-GIL support should be honest about the first failing trace. If the original
bug depends on an unguarded shared-object race, Retrace may not be able to
deterministically recreate that exact interleaving from the first recording.

The useful product outcome is still concrete:

```text
We could not force the exact no-GIL schedule from this trace.
This object was accessed by threads A and B without a recorded synchronization
handoff. Add or move synchronization here.
```

After the customer fixes the missing guard, future recordings should become more
deterministic because the relevant shared-memory ordering is now represented by
a lock, queue, event, condition, future, or other recorded synchronization
handoff. If the bug happens again after that fix, Retrace has a much better
chance of replaying it deterministically.

This makes the diagnostic actionable even when the first trace is not a perfect
reproduction artifact.

## Thread Progress Counters

Each Retrace-managed thread maintains a monotonic progress counter.

```c
struct RetraceThreadProgress {
    _Atomic uint64_t published;
    uint64_t local;
};
```

The owning thread advances progress at cursor points:

```c
progress->local++;
atomic_store_explicit(&progress->published,
                      progress->local,
                      memory_order_relaxed);
```

Other threads can cheaply sample progress:

```c
uint64_t seen = atomic_load_explicit(&other->published,
                                     memory_order_relaxed);
```

The sampled value is a lower bound:

```text
thread B observed that thread A had reached at least progress P
```

A stale read is acceptable because it still records a true lower bound. It may
be too weak to reproduce a race by itself, so progress edges are used as search
constraints, not as the only replay contract.

The progress table must outlive thread exit long enough to avoid stale owner ids
mapping to a different thread.

## Object Escape Detection

New objects normally start biased to the creating thread. A CPython patch can
use the non-owner refcount path as a cheap cross-thread observation hook:

```text
object X is biased to thread A
thread B touches X through a non-owner path
sample A.progress = P
emit: B observed escaped object X, requiring A >= P
```

This hook should not allocate, call Python, or pass through Retrace application
I/O. It should write to a low-level telemetry buffer or set side metadata.

`ob_tid` should be treated as a hint only:

- If `ob_tid` names another live Retrace thread, it can identify a useful
  progress counter to sample.
- If `ob_tid` is zero or otherwise unavailable, the object may already be
  unowned or merged.
- If an object is immortal or common runtime state, the signal may not be useful.

Retrace-owned object metadata should live outside the `PyObject` header, for
example in a side table or allocator/page metadata.

## Object Classification

The runtime classifies objects dynamically:

| Class | Meaning |
| --- | --- |
| private | Created and observed by one thread only. |
| escaped | Observed by at least two threads. |
| escaped-synchronized | Meaningful access happens under known synchronization. |
| escaped-unsynchronized | Access or mutation happens without a known protecting lock. |
| unknown | Native or extension access was observed but cannot be classified. |

Only escaped mutable or unknown objects are replay-sensitive. Private objects do
not require scheduler search.

## Lockset Tracking

Each thread maintains a current lockset:

```text
thread T holds {lock#3, lock#7}
```

Retrace records linearization points for synchronization primitives:

- `Lock.acquire` and `release`
- `RLock`
- `Condition.wait`, `notify`, and `notify_all`
- `Event`
- `Semaphore`
- `Barrier`
- `Queue.put` and `get`
- `Thread.start` and `join`
- `concurrent.futures` task handoff and result delivery

At object access points, the runtime can classify:

```text
object X escaped
thread B reads or mutates X
current lockset is empty
=> escaped-unsynchronized access
```

This is Python-level race detection. It is similar in spirit to ThreadSanitizer,
but the unit is Python object/protocol access rather than raw memory.

## Synchronization Handoffs

Each synchronization object should carry small side metadata describing the last
operation that made progress available to another thread. When another thread
consumes that progress, Retrace records the handoff.

For a simple lock:

```text
lock#7.last_unlock = {
  thread: A,
  progress: 120,
  cursor_ref: anchor 57 + delta dA,
  sequence: 33
}

thread B successfully acquires lock#7
=> record B acquired lock#7 from A@120/(anchor 57 + dA)
```

If a lock is released and reacquired by the same thread, the handoff is usually
not scheduler-interesting. If another thread acquires it, the acquire observes a
real ordering fact:

```text
A unlocked lock#7 before B acquired lock#7
```

During replay, this edge can be enforced without a global epoch:

```text
do not let B pass acquire(lock#7, seq=34)
until A has reached the recorded unlock progress/cursor reference
```

The same pattern generalizes to synchronization objects:

| Primitive | Handoff metadata |
| --- | --- |
| `Lock` | Last successful unlock at depth zero. |
| `RLock` | Last final unlock, ignoring recursive releases. |
| `Condition` | Notify generation, notifying thread, wait result, and lock reacquire handoff. |
| `Semaphore` | Token release sequence and releasing thread for each consumed token. |
| `Event` | Set generation and setting thread observed by `wait`. |
| `Barrier` | Generation completion thread and released generation. |
| `Queue` | Put sequence, producer thread, and consumed item sequence. |
| `Future` | Completion thread and result/exception delivery sequence. |

This turns synchronization replay into a graph of local causal edges:

```text
A release(lock#7, seq=33) -> B acquire(lock#7, seq=34)
C put(queue#2, item=88)   -> D get(queue#2, item=88)
E set(event#5, gen=12)    -> F wait(event#5, gen=12)
```

These edges also help race detection. If an escaped object is accessed by two
threads and the accesses have no common lockset and no synchronization handoff
ordering them, the object belongs in the escaped-unsynchronized class.

The side metadata should not live in the public object header. It can live in
Retrace side tables keyed by synchronization object identity, with sequence
numbers to avoid stale reuse.

### Eager Handoff State

A lock cannot detect a cross-thread handoff unless it remembers who last
released it. This state must be available on the acquire/release fast path; it
cannot depend entirely on lazy allocation after the handoff has already been
noticed.

The eager state should be the minimum needed to detect and describe the next
handoff:

```text
last_releasing_thread_id
last_releasing_progress
last_releasing_cursor_ref
last_release_sequence
```

On release:

```text
capture current thread/progress/cursor_ref
store it into the lock handoff state
then release the underlying lock
```

On successful acquire:

```text
if last_releasing_thread_id != current_thread_id:
    invoke the lock-handoff callback
```

The lock's own release/acquire ordering should make the previous release metadata
visible to the acquiring thread. Failed non-blocking acquires do not consume a
handoff. Recursive locks should update the release metadata only on the final
unlock that makes the lock available to other threads.

Lazy storage still has a role, but only for overflow:

- long cursor deltas,
- optional stack/debug context,
- rare synchronization types with large metadata,
- cold locks if the runtime chooses to allocate a sidecar at lock creation.

In other words, handoff detection is eager; expensive handoff detail is lazy.

### Handoff Callback

The lock path should not write a trace event on every acquire/release. It should
invoke a low-level callback only when a lock changes hands:

```c
typedef void (*RetraceLockHandoffCallback)(
    PyObject *lock,
    uint64_t lock_access_counter,
    uint64_t previous_thread_id,
    uint64_t previous_progress,
    uint64_t previous_cursor_hash,
    uint64_t current_thread_id,
    uint64_t current_progress,
    uint64_t current_cursor_hash
);
```

The callback writes or buffers a `SYNC_HANDOFF` event:

```text
SYNC_HANDOFF(
  sync_id=lock#7,
  kind=lock,
  sequence=42,
  from_thread=A,
  from_progress=120,
  from_cursor_hash=hA,
  to_thread=B,
  to_progress=44,
  to_cursor_hash=hB
)
```

The callback must be safe for a hot synchronization path:

- no Python calls,
- no allocation on the common path,
- no application-visible locks,
- no ordinary Retrace gate/proxy routing,
- bounded work, preferably a write into a per-thread or lock-free telemetry
  buffer.

If the callback cannot enqueue the event without blocking, it should either drop
only optional debug detail or mark the trace as needing a slower fallback mode.
It must not silently drop the causal handoff edge in a mode that promises
handoff replay.

### Minimal Lock Metadata

The smallest useful Python-lock metadata is:

```text
last_locking_thread_id
lock_access_counter
```

On every successful acquire, increment `lock_access_counter` and remember the
thread that acquired the lock. On the next successful acquire, if
`last_locking_thread_id` differs from the current thread, the lock changed
hands.

This is attractive because it is tiny and cheap, but it is only a handoff
detector. It is not enough to robustly replay divergent paths:

```text
record:
  lock#7 access_counter=42 acquired by thread B

replay:
  thread B also needs to acquire lock#7 access_counter=42,
  but B reached that acquire through a different cursor/path
```

The counter can tell replay that the same synchronization object reached the
same acquire ordinal. It cannot prove that the acquiring thread is at the same
program point. For deterministic replay and schedule search, pair the counter
with a progress/cursor reference when possible:

```text
lock_access_counter
last_locking_thread_id
last_progress
last_cursor_ref
```

The minimal form may still be useful in a low-overhead race-detect mode, where
the goal is to identify handoffs and suspicious unsynchronized access rather
than guarantee a precise replay cursor.

### Counter Plus Cursor Hash

A stronger low-overhead variant stores an ordinal plus a cursor fingerprint
instead of copying the cursor:

```text
last_locking_thread_id
lock_access_counter
last_progress
last_cursor_hash
```

`lock_access_counter` says which acquire/release ordinal the lock reached.
`last_cursor_hash` says whether the thread was probably at the same cursor path
when it touched the lock.

This avoids copying a long cursor into the lock on every access. The lock path
only stores fixed-size words. Full cursor materialization can be deferred until:

- a cross-thread handoff is detected,
- replay sees a counter/hash mismatch,
- search mode decides the handoff is inside a suspicious window,
- debug mode explicitly requests full cursor telemetry.

The hash is not an ordering primitive. It validates path identity probabilistically.
Use the progress counter or cursor reference for "at least this far" ordering.
For practical replay artifacts, a hash match can keep the fast path compact, and
a hash mismatch can force a full cursor snapshot or schedule-search branch.

The cursor hash should include the function-count stack and `f_lasti` when
available. A 64-bit hash is probably enough for a fast-path guard; a stronger
hash or full cursor reference may be needed for persisted replay artifacts where
collisions are unacceptable.

### Cursor Storage

Synchronization objects should not store full long cursors when a smaller
reference is enough. Long cursor tuples can be expensive if every contended lock,
queue, condition, and future stores one.

Use recent access cursors as the primary compression anchors, with epochs as a
fallback:

```text
thread A access anchor 57 cursor = AA
thread A unlock cursor = AA + delta dA

lock#7.last_unlock = {
  thread: A,
  progress: 120,
  anchor_sequence: 57,
  cursor_delta: dA,
  sequence: 33
}
```

If a thread repeatedly touches synchronization objects from the same region of
code, the cursor delta from its last access anchor should usually be very small.
The full cursor is reconstructed from:

```text
access_anchor(thread=A, sequence=57) + cursor_delta
```

The anchor must be stable. Do not delta from a mutable "current cursor" slot
without also recording which anchor sequence the delta is relative to; by the
time another thread consumes the lock handoff, the unlocking thread may have
advanced through many unrelated cursors.

The trace format and in-memory side tables should allow a fallback to a full
cursor when:

- no usable access or epoch anchor exists,
- the delta is larger than the full cursor,
- the delta crosses a cursor shape change that is hard to encode compactly,
- the referenced anchor has been evicted from the in-memory cache.

Epoch anchors are still useful as periodic reset points. They cap the length of
anchor chains and give the replay/search engine coarse progress markers, but hot
synchronization paths should normally delta from the previous access by the same
thread.

Progress counters remain useful even when the cursor is compressed. The progress
value is the cheap ordering check; the cursor reference is the replay target.

### Expected Delta Shape

The useful empirical question is not full cursor depth, but how much the cursor
changes between two synchronization accesses by the same thread.

With the current cursor model, a cursor contains:

```text
function_counts: [root_count, ..., leaf_count]
f_lasti: optional bytecode offset
```

For repeated synchronization access in a loop or a stable call site, the delta
should often be tiny:

```text
same stack shape
same prefix
leaf count or f_lasti changes
=> 1 to 3 small values
```

For nested calls inside a critical section, condition callbacks, queue/future
handoffs, or accesses that happen after returning to a different frame, the
delta may need more lanes. This should be measured against real recordings
before choosing the inline size.

A practical delta format is:

```text
common_prefix_length
replacement_suffix_values
optional f_lasti_delta_or_value
```

If most replacement suffix elements are below `65536`, a `uint16_t` lane format
is likely dense enough. Larger counts or shape changes use overflow storage.

### Inline Cursor Deltas

The common lock handoff record should be small. A synchronization object or its
side metadata can usually store:

```text
last_thread_id: 64 bits
last_progress: 64 bits
last_sequence: 64 bits
last_cursor_delta: inline small delta or overflow reference
```

The cursor delta can use a fixed inline payload before falling back to heap or
side-table storage. One possible two-word encoding:

```text
word0 != UINT64_MAX:
    inline payload across word0 and word1
    treat payload as eight 16-bit lanes
    lane 0 = cursor delta length
    lanes 1..7 = cursor delta values

word0 == UINT64_MAX:
    word1 = overflow pointer or side-table handle
```

If real cursor deltas often need more than seven values, the same scheme can use
four 64-bit words instead:

```text
lane 0 = cursor delta length
lanes 1..15 = cursor delta values
```

The two-word form is more attractive for hot synchronization metadata. The
four-word form is more likely to keep almost all access-relative deltas inline.
Both preserve the important property: the usual handoff path does not allocate.

This encoding assumes cursor deltas are non-negative and each element usually
fits in `uint16_t`. If a delta element is larger, has a shape that cannot be
encoded as small unsigned lanes, or would make the inline representation larger
than the full cursor, use the overflow path.

## Access Instrumentation

Access instrumentation should be selective.

Start with operations where CPython already has clear semantic boundaries:

- dict lookup, set, delete, and iteration
- list read, append, set, pop, and iteration
- set membership, add, remove, and iteration
- object attribute read and write
- descriptor get/set
- queue transfer
- callback crossing between threads

For extension types, begin conservatively. If an extension accesses an escaped
object in a way Retrace cannot classify, mark the object or type as `unknown`
and bias schedule search around that access.

Reads matter. A write-only log cannot reproduce a race where a branch depends on
which value a thread read.

## Epochs

Epochs are optional coarse search hints and cursor-compression anchors, not the
primary causality mechanism. Synchronization handoffs give better local ordering
for normal programs. Epochs may still be useful when search needs broad progress
markers around unknown native code or unsynchronized access clusters.

### Soft Epoch

A soft epoch is a low-overhead lower-bound vector.

```text
request epoch E
thread A later acknowledges E at progress 100
thread B later acknowledges E at progress 44
```

This means:

```text
A reached at least 100
B reached at least 44
```

It is not a simultaneous snapshot. Soft epochs are suitable for production:
threads do not stop, and the normal cost is only a cheap epoch flag check at
safe points.

### Thin Hard Epoch

A thin hard epoch is a two-phase rendezvous intended to reduce stop time.

Normal mode:

```c
if (unlikely(epoch_unsynced)) {
    enter_epoch_monitoring_mode();
}
```

Monitoring mode:

```text
threads continue executing bytecode
threads pay a memory-boundary/checkpoint cost at each eval checkpoint
each participating thread marks itself as monitoring
```

Capture phase:

```text
when all live/accounted threads are monitoring:
    coordinator flips capture flag
    each thread snapshots at its next checkpoint
    each thread parks until release
```

The expensive memory boundary is paid only during monitoring mode, which should
be short. The final pause is bounded by the next eval checkpoint for each
participating thread. In practice this is not always one bytecode because calls
into C extensions or blocking operations may delay the next checkpoint.

Thin hard epochs are useful for debugging, not as the default production mode.

### Blocked Threads

Threads blocked in known blocking calls can be accounted for without waking:

```text
thread T state = blocked
at = Queue.get(queue#5)
progress = P
```

Threads inside unknown native code are harder. An epoch must either wait for
them, mark them as unaccounted, or treat their next return to Python as the
acknowledgment point.

## Schedule Search

For synchronized programs, replay enforces recorded synchronization order.

For escaped-unsynchronized objects, replay can first produce a diagnostic. If a
recorded failure is hard to reproduce because replay reaches a schedule-sensitive
state, the analysis should identify:

- the escaped object,
- the threads that accessed it,
- the read/write or mutation operations involved,
- the nearest progress/cursor/hash for each access,
- the locksets held by each thread,
- the missing synchronization handoff that would have ordered the accesses.

When useful, replay can then search for a schedule that recreates the failure:

```text
1. Replay deterministic external inputs.
2. Enforce recorded synchronization handoff constraints.
3. Use object-escape and unsynchronized-access telemetry to choose a search window.
4. Branch scheduler choices near escaped-unsynchronized access.
5. Stop when the failure oracle matches the recorded failure.
6. Save the found schedule as a deterministic replay artifact.
```

The goal is not to rediscover the exact original CPU interleaving. The goal is
to find a legal schedule under the same deterministic inputs that reproduces the
same failure.

If search cannot cheaply reproduce the failure, the race diagnostic is still a
valuable result. It gives the user a concrete missing-lock or unguarded-access
candidate instead of a vague nondeterministic replay miss.

Useful failure oracles include:

- uncaught exception type and location
- failed assertion
- process exit status
- deadlock or timeout signature
- divergent external output
- user-supplied predicate

## Handoff-Guided Search

Synchronization handoffs divide execution into causally ordered regions. Search
should preserve those edges and branch only where the trace leaves ordering
freedom.

```text
A unlocks lock#7      B acquires lock#7
       |-------------------|
         fixed handoff edge
```

The search engine can start with a failure window bounded by external events and
synchronization handoffs, then narrow around escaped-unsynchronized access:

```text
external event 200..260: failure possible
object X first escapes at event 221
object X has unsynchronized read/write at events 232 and 238
branch schedules around 232..238
```

Within the selected interval, branch priority should favor:

- first escape of an object
- escaped-unsynchronized reads or writes
- conflicting accesses where at least one access mutates
- lock contention
- queue transfer ordering
- callbacks crossing between threads
- external boundary calls from multiple threads
- optional soft-epoch markers near unknown native code

Partial-order reduction should avoid exploring both orders of operations that
cannot affect each other.

## Trace Events

Possible new event shapes:

```text
THREAD_PROGRESS(thread, progress, cursor)
OBJECT_ESCAPE(object_id, owner_thread, observer_thread,
              owner_progress_seen, observer_progress, cursor)
SYNC_ACQUIRE(thread, lock_id, result, progress, cursor)
SYNC_RELEASE(thread, lock_id, progress, cursor)
SYNC_HANDOFF(sync_id, kind,
             from_thread, from_progress, from_cursor_ref,
             to_thread, to_progress, to_cursor_ref,
             from_cursor_hash, to_cursor_hash,
             sequence)
OBJECT_ACCESS(object_id, thread, access_kind, lockset_id,
              progress, cursor)
EPOCH_SOFT(epoch_id, thread, progress, cursor)
EPOCH_HARD_CAPTURE(epoch_id, thread, progress, cursor, state)
SCHEDULE_CHOICE(thread, progress, cursor)
```

Not every event needs to be emitted in every mode. Production mode can record
external and synchronization handoff events. Debug modes can add object access,
soft epochs, and schedule choice telemetry.

## Modes

| Mode | Behavior |
| --- | --- |
| normal | Record external effects and synchronization events. |
| race-detect | Add escape detection, locksets, and escaped-unsynchronized warnings. |
| search | Use deterministic trace plus scheduler search to recreate a failure. |
| strict | Add object access/version events for selected shared objects. |
| serialized | Use a Retrace scheduler lock around monitored execution. |

`serialized` mode gives the strongest replay guarantees but sacrifices no-GIL
parallelism. It is a last resort for debugging.

## CPython Patch Points

Likely patch points:

- non-owner biased-refcount paths for first cross-thread observation
- eval-loop safe points for progress and optional epochs
- thread start/exit registration for progress tables
- synchronization primitive release/acquire handoff paths
- built-in container operation boundaries
- object attribute get/set paths
- blocking synchronization primitives

The refcount hook must be extremely small. It should not allocate, call Python,
take application locks, or emit normal Retrace protocol messages directly.

## ABI Constraint

Do not add fields to `PyObject`.

Adding fields changes offsets compiled into extension wheels and would require a
custom ABI and packaging story. Source compatibility is not enough; binary wheels
compiled for stock free-threaded CPython must not be loaded into a runtime with a
different object layout unless the SOABI and wheel tags make that impossible.

Use side metadata instead.

## Risks

- `ob_tid` is an implementation detail and can be reused for GC/trashcan paths.
- Some objects are immortal, unowned, or merged before the interesting access.
- Borrowed references and optimized stack references may bypass desired hooks.
- C extensions may mutate state without visible Python object operations.
- Stale progress samples create true but weak ordering constraints.
- Schedule search can explode without synchronization handoffs and
  partial-order reduction.
- Instrumentation can perturb the race being debugged.

## Open Questions

- What is the minimum useful cursor granularity for no-GIL schedule search?
- Which CPython access points provide enough read/write classification without
  excessive overhead?
- How should Retrace identify common runtime objects that should not trigger
  escape tracking?
- Can object metadata be stored efficiently in allocator/page metadata rather
  than a global side table?
- How should schedule artifacts be represented so a found schedule can be
  replayed deterministically?
- Should race-detect mode be available on stock CPython, or only on a patched
  Retrace CPython build?
