# Thread-Aware Record/Replay

The trace file is a single interleaved stream of events from all threads.
During recording, `ThreadSwitch` markers are inserted whenever the writing
thread changes.  During replay, a **demultiplexer** reads those markers and
routes each message to the correct thread, reproducing the exact recorded
interleaving.

## Why real locks aren't needed during replay

The trace file has a **total ordering** over all events, including lock
acquisitions, releases, condition waits, and thread switches.  That
ordering *is* the synchronisation mechanism.  The demux makes each thread
block until the tape cursor reaches a segment that belongs to it, so
threads advance in exactly the order they did during recording.  No real
lock operations need to execute — the recorded return values are simply
replayed while the demux enforces the schedule.

## Architecture

```
Record                                  Replay
──────                                  ──────

  thread A ──┐                            trace.bin
  thread B ──┤ stream.writer             ┌──────────┐
  thread C ──┘  (thread=utils.thread_id) │ header   │
       │                                 │ SYNC ... │
       │  check_thread() detects         │ TS(())   │──→ stream.reader
       │  PyThreadState changes,         │ RESULT ..│       │
       │  writes ThreadSwitch(id)        │ TS((0,)) │       │
       │  markers into the binary        │ SYNC ... │   stream.per_thread
       ▼  stream                         │ TS(())   │    (StickyPred + demux)
                                         │ RESULT ..│       │
   trace.bin                             └──────────┘   ┌───┴───┐
                                                        │       │
                                                      main   thread (0,)
                                                      demux   demux
                                                        │       │
                                                    MessageStream MessageStream
                                                        │       │
                                                      replay  replay
```

## Recording

Three pieces cooperate to produce a thread-aware trace:

### 1. Hierarchical thread IDs (`install/__init__.py`)

`run_with_context` creates a `_thread._local()` counter that assigns each
new thread a deterministic hierarchical ID based on birth order:

```
Main thread  → ()
1st child    → (0,)
2nd child    → (1,)
grandchild   → (0, 0)
```

The wrapper that `patch_thread_start` installs does two things in the
parent thread before starting the child:

1. Computes `next_id = parent_id + (counter,)` and increments the counter.
2. Returns a wrapped function that, in the child thread, sets the ID via
   `utils.set_thread_id(next_id)` before entering the record/replay
   context.

`utils.set_thread_id` stores the ID in `PyThreadState_GetDict()` under
the `utils` module as key.  `utils.thread_id()` reads it back.  The main
thread is initialised to `()` at the start of `run_with_context`.

### 2. Stream writer thread detection (`stream/cpp/objectwriter.cpp`)

`stream.writer` is created with `thread=utils.thread_id`:

```python
stream.writer(path=trace_path, thread=utils.thread_id, ...)
```

The C++ `ObjectWriter` stores this as a `FastCall` member.  On every
write, `check_thread()` compares `PyThreadState_Get()` against the last
seen thread state.  When it changes:

1. Calls `thread()` → `utils.thread_id()` → the hierarchical tuple.
2. Creates (or retrieves a cached) `StreamHandle` for that ID.
3. Writes a binary `ThreadSwitch` control marker into the stream.

This is zero-cost when only one thread writes — `check_thread()` is a
pointer comparison that short-circuits.

### 3. Binary encoding (`stream/cpp/wireformat.h`)

The wire format uses a control byte to distinguish `ThreadSwitch` from
regular data.  The reader reconstructs these as `stream.ThreadSwitch`
Python objects (a `Control` subclass wrapping the thread ID value).

## Replay

### 1. `stream.per_thread` — file-backed demux (`stream/__init__.py`)

```python
per_thread_source = stream.per_thread(
    source=reader, thread=utils.thread_id,
    timeout=args.read_timeout // 1000)
```

This builds a pipeline:

- **`StickyPred`**: A key function that tracks the "current" thread.
  For `ThreadSwitch` objects it extracts the new ID; for everything else
  it returns the last seen ID.  Initialised with `thread()` (the main
  thread's ID).
- **`utils.demux`** (C extension): Buffers messages keyed by
  `StickyPred`.  When thread *T* calls `demux(T)`, it returns the next
  message with key *T*.  If no message is buffered, it pulls from the
  source until one arrives (reading through other threads' messages and
  buffering them).  Has a configurable timeout.
- **`drop`**: Filters out `ThreadSwitch` objects so `MessageStream`
  never sees them.
- **`functional.sequence(thread, demux)`**: Composes into
  `demux(utils.thread_id())` — each call routes to the current thread.

The returned callable is passed to `MessageStream(per_thread_source)`.
Under the GIL, only one thread runs at a time, so `MessageStream` methods
(which make multiple `source()` calls per message) are effectively atomic.

### 2. Thread ID matching

Replay threads get the same hierarchical IDs as recording threads because
`run_with_context` applies the same `patch_thread_start` wrapper.  When a
replay thread calls `utils.thread_id()`, it gets the same tuple that the
recording thread had, so the demux routes it to the correct messages.

### 3. Contrast with in-memory demux

The in-memory backend (`proxy/messagestream.py`) uses `_TapeDemux` — a
pure-Python demultiplexer that walks a list and uses `threading.Event` to
block/wake threads.  It uses `ThreadSwitchMessage` objects (distinct from
`stream.ThreadSwitch`) because the in-memory tape is a plain Python list,
not a binary stream.

The file-backed path uses `stream.per_thread` + `utils.demux` instead,
because:

- The source is a C++ `ObjectStreamReader`, not a Python list.
- `ThreadSwitch` markers are `stream.ThreadSwitch(Control)` objects.
- `utils.demux` handles blocking with a C-level timeout.

Both paths produce the same result: each thread gets a `MessageStream`
that yields only its own messages in recorded order.

## Data flow (single-threaded vs multi-threaded)

### Single-threaded recording

No `ThreadSwitch` markers are written (the `PyThreadState` never changes).
`stream.per_thread` still works: `StickyPred` keeps its initial value
(the main thread's ID) for every message, the demux assigns all messages
to the main thread, and `drop` has nothing to filter.

### Multi-threaded recording

```
tape: [header] [TS(())] [SYNC] [RESULT v1] [TS((0,))] [SYNC] [RESULT v2] [TS(())] [SYNC] [RESULT v3] ...
```

`TS(())` means "following messages belong to thread `()`".  The demux
reads sequentially, buffers messages for other threads, and delivers
them when requested.

## Synchronisation guarantees

The demux enforces the exact recorded schedule:

- If thread A acquired a lock before thread B during recording, the tape
  has A's `acquire → True` result before B's.  During replay, B blocks
  in the demux waiting for its turn — it cannot advance past A.

- Condition variable waits, lock releases, and all other synchronisation
  primitives are recorded as ordinary proxied call results.  The demux
  ordering substitutes for the real synchronisation.

- The only real synchronisation during replay is inside `utils.demux`
  itself (a mutex protecting the shared cursor and per-thread buffers).

## Related files

| File | Role |
|------|------|
| `retracesoftware/__main__.py` | `record()`: passes `thread=utils.thread_id` to writer. `replay()`: wraps reader with `stream.per_thread`. |
| `install/__init__.py` | `run_with_context`: assigns hierarchical thread IDs, calls `utils.set_thread_id`. |
| `install/startthread.py` | `patch_thread_start`: patches `_thread.start_new_thread` to wrap new threads. |
| `stream/__init__.py` | `per_thread()`: file-backed demux using `StickyPred` + `utils.demux`. `ThreadSwitch(Control)`: marker class. |
| `stream/cpp/objectwriter.cpp` | `check_thread()`: detects thread changes, writes binary `ThreadSwitch` markers. |
| `utils/cpp/module.cpp` | `thread_id()` / `set_thread_id()`: C-level per-thread ID storage in `PyThreadState_GetDict`. |
| `proxy/messagestream.py` | `_TapeDemux`: in-memory equivalent using `threading.Event`. `ThreadSwitchMessage`: in-memory marker. |
| `proxy/thread.py` | `per_thread_messages()`: older demux using `prefix_with_thread_id` + `utils.demux`. |
| `proxy/docs/THREAD_IDS.md` | Documents the hierarchical ID scheme. |
