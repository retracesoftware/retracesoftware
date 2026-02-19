# Debugging Retrace

This guide covers how to diagnose failures in retrace recording and
replay.  It is aimed at retrace developers, not end-users.

---

## Quick-start checklist

When something breaks, enable all diagnostics first:

```bash
RETRACE_DEBUG=1 python -m retracesoftware \
    --recording /tmp/trace.bin \
    --verbose \
    --stacktraces \
    -- my_script.py
```

For replay:

```bash
RETRACE_DEBUG=1 python -m retracesoftware \
    --recording /tmp/trace.bin \
    --verbose
```

`RETRACE_DEBUG=1` is set automatically for all tests via `conftest.py`.

---

## Diagnostic flags

### Environment variables

| Variable | Effect |
|---|---|
| `RETRACE_DEBUG=1` | Loads `_retracesoftware_{stream,utils,functional}_debug` builds with C/C++ assertions enabled and debug symbols.  Without this, the `_release` builds are loaded. |

### CLI flags (record)

| Flag | Effect |
|---|---|
| `--verbose` | Prints every message the writer emits to stdout, tagged with PID, message index, and byte offset. |
| `--stacktraces` | Captures a stack delta for every proxied call and writes it to the trace as a `STACKTRACE` message.  Invaluable for identifying *where* a particular message originates. |
| `--write_timeout N` | Backpressure timeout in seconds.  `0` = drop immediately, omit = wait forever. |
| `--workspace PATH` | Generate a VS Code workspace directory with sidecar files (`settings.json`, `.env`, checksums, launch config). |
| `--monitor N` | Enable `sys.monitoring` divergence detection (Python 3.12+).  `0` = off (default), `1` = Python calls/returns, `2` = + C calls, `3` = + line events.  See [Monitor mode](#7-using-monitor-mode) below. |

### CLI flags (replay)

| Flag | Effect |
|---|---|
| `--verbose` | Enables verbose output on the reader.  Prints every consumed message with byte offsets and message indices. |
| `--read_timeout N` | Milliseconds to wait for an incomplete read before raising a timeout error (default 1000). |
| `--fork_path PATH` | Binary string (`010`, `111`, etc.) or keyword (`child`, `parent`) controlling which fork branch to follow.  `0` = parent, `1` = child. |
| `--list_pids` | Scans the trace file and prints all unique PIDs, then exits. |
| `--skip_weakref_callbacks` | Disables retrace in weakref callbacks on replay. |

---

## Verbose output format

### Writer (record)

Each line is printed by the C++ `ObjectWriter`:

```
Retrace(PID) - ObjectWriter[MSG_IDX, BYTE_OFFSET] -- PAYLOAD
```

Examples:

```
Retrace(12345) - ObjectWriter[0, 4545] -- {'argv': [...], ...}
Retrace(12345) - ObjectWriter[1, 4545] -- NEW_HANDLE(ERROR)
Retrace(12345) - ObjectWriter[8, 4624] -- BIND(_thread.RLock)
Retrace(12345) - ObjectWriter[9, 4650] -- RESULT
Retrace(12345) - ObjectWriter[10, 4654, 4698] -- os.getcwd()
Retrace(12345) - ObjectWriter[11] -- THREAD_SWITCH(())
```

Two byte offsets (`4654, 4698`) mean the object spanned those bytes.
`BIND(type)` / `EXT_BIND(type)` show class bindings.  `DELETE(id)`
shows handle deallocation.

### Reader (replay)

```
Retrace - ObjectStream[MSG_IDX, BYTE_OFFSET] - Consumed NEW_HANDLE -> read N bytes
Retrace - ObjectStream[MSG_IDX, BYTE_OFFSET] - Consumed EXT_BIND
Retrace - ObjectStream[MSG_IDX, BYTE_OFFSET] - Consumed STACK - drop: 2
Retrace - ObjectStream[MSG_IDX, BYTE_OFFSET] - Read BIND
Retrace - ObjectStream[MSG_IDX, BYTE_OFFSET] - Read: 42
```

With `verbose > 1` (set via `reader.verbose = 2`), low-level control
bytes are printed:

```
  consume: control 0x3A at byte 9845
```

---

## Failure modes

### 1. Replay divergence

**Symptoms:**

```
ReplayDivergence: replay divergence: expected '/tmp/foo', got '/tmp/bar'
```

This is a *checkpoint mismatch*.  During recording, the proxy writes
`CHECKPOINT(normalize(value))` after each external call.  During replay,
the same normalization is applied to the replay value and compared.

**Common causes:**

- **Non-deterministic C calls** — `time.time()`, `random.random()`,
  `uuid.uuid4()`.  These must be intercepted; if a module's TOML config
  is missing them, the replay value will differ.
- **Hash ordering** — Dict iteration order depends on hash values.
  Retrace patches hashes to be deterministic, but if a module is loaded
  before patching (or isn't patched at all), dict order diverges.
- **Filesystem state** — `os.stat()`, `os.listdir()`, etc. return
  live values.  During record these are captured; during replay the
  intercepted call returns the recorded value, but if the call isn't
  intercepted, the live value leaks through.
- **Environment differences** — Different `cwd`, env vars, or Python
  version between record and replay.  Retrace checks `python_version`
  and module checksums at replay start.

**How to investigate:**

1. Enable `--verbose` on both record and replay.
2. Compare the writer output from record with the reader output from
   replay.  The message indices should march in lockstep.
3. Enable `--stacktraces` on the recording.  On replay with
   `--verbose`, the reader prints stack frames for each message,
   showing the Python call site where the divergent value was produced.
4. The `ReplayDivergence` message includes both the expected (recorded)
   and actual (replayed) values.

### 2. Tape misalignment (unexpected message type)

**Symptoms:**

```
RuntimeError: Can't reading next as unbound pending bind
```

or the reader returns a `Bind` / `HandleMessage` / `ThreadSwitch`
where the replay code expected a `RESULT` or `SYNC`.

**What this means:**

The reader's position in the trace has drifted out of sync with the
replay execution.  The message stream is a flat sequence:

```
SYNC RESULT value SYNC RESULT value SYNC ERROR exc ...
```

During replay, `MessageStream.sync()` advances to the next `SYNC`
marker, then `result()` reads the next `RESULT` or `ERROR`.  If the
replay takes a different code path than the recording (e.g. an
un-intercepted non-deterministic call changes control flow), the
reader lands on the wrong message.

**Common causes:**

- **Un-intercepted non-deterministic calls** — A C extension call
  that isn't proxied returns a different value on replay, changing
  which branch Python takes, which changes how many proxied calls
  happen before the next checkpoint.
- **Missing module config** — A C extension module used by the
  program isn't listed in the TOML configs, so its calls pass
  through unrecorded.
- **Bind protocol violation** — After a PID switch in fork replay,
  the child's stream may contain `Bind` markers for classes bound
  after the fork (e.g. `_thread.RLock` from CPython's post-fork
  threading cleanup).  `MessageStream._next_message()` must
  handle these by calling `tag.value(None)` to clear the reader's
  `pending_bind` flag.

**How to investigate:**

1. Record with `--verbose --stacktraces`.
2. Replay with `--verbose`.
3. Find the last successful `SYNC`/`RESULT` pair in both logs.
   The message *after* that is where alignment broke.
4. The stack trace on the recording side (from `--stacktraces`)
   tells you which Python call site produced the divergent message.
5. With `RETRACE_DEBUG=1`, C++ assertions in the reader catch
   protocol violations early.

### 3. Read timeout

**Symptoms:**

```
RuntimeError: Could not read: 28244 bytes from tracefile with timeout: 1000 milliseconds
```

**What this means:**

The reader tried to read more data than exists in the trace.  Either
the recording was truncated (crash, SIGKILL), or the replay has
drifted past the end of the trace due to misalignment.

**How to investigate:**

- If the recording crashed, check stderr from the recording process.
- If the recording completed normally, this is a misalignment issue —
  see "Tape misalignment" above.
- Increase `--read_timeout` if the recording writes to a pipe and
  the writer is slow.

### 4. PID mismatch in persister (debug builds only)

**Symptoms:**

```
AsyncFilePersister: PID mismatch! frame stamped 12345 but process is 12346
```

followed by an assertion failure / abort.

**What this means:**

The `AsyncFilePersister` stamps each frame with the process PID via
`stamp_pid()` at `init()` and `resume()` time.  If a frame is being
written with a stale PID (from before a fork), the assert fires.

This indicates a problem in the drain/resume fork lifecycle:
`_before_fork()` should `drain()` the writer (stopping the background
thread), and `_after_fork_child()` should `resume()` (re-stamping PID
and starting a new thread).  If these hooks aren't running, or are
running out of order, frames can be stamped with the wrong PID.

**How to investigate:**

- Verify `os.register_at_fork` hooks are installed (check
  `stream/__init__.py` writer init).
- Verify the drain/resume cycle with print statements in
  `_before_fork` / `_after_fork_child`.

### 5. Fork replay errors

**Symptoms:**

Various — `RuntimeError`, `ReplayDivergence`, wrong output, or
following the wrong process.

**Key concepts:**

During replay, `os.fork()` is replaced by `make_replay_fork()`.  The
`--fork_path` argument controls which branch to follow at each fork:

```
--fork_path 010    # parent at fork 0, child at fork 1, parent at fork 2
```

The wrapper calls the real `os.fork()`, then:
- If following child: calls `reader.set_pid(child_pid)` to switch
  the reader's PID filter, returns `0`.
- If following parent: returns `child_pid` unchanged.

**Common issues:**

- **`os.fork` not patched** — The `install_fork_handler` must patch
  both `posix.fork` AND `os.fork`, since user code may call either.
- **Bind markers after PID switch** — After switching to the child's
  frames, the reader may encounter `Bind` tags for classes the child
  bound after forking (e.g. `_thread.RLock` from CPython's internal
  `threading._after_fork_child()`).  `MessageStream._next_message()`
  must handle these.
- **Orphaned RESULT(0)** — The child's first message is the fork
  return value `RESULT(0)`.  `MessageStream.sync()` naturally skips
  it.  Do NOT try to consume it explicitly.

**How to investigate:**

1. Record with `--verbose` to see which PIDs emit which messages
   (each line is tagged `Retrace(PID)`).
2. Use `--list_pids` to enumerate all PIDs in the trace.
3. Replay with `--verbose --fork_path <path>` to watch the PID
   switch happen and see what messages the reader encounters.

### 6. Thread replay deadlock / timeout

**Symptoms:**

```
ReplayDivergence: replay demux timed out after 30s waiting for thread main-thread
```

or the replay hangs indefinitely.

**What this means:**

Multi-threaded replay uses `ThreadSwitchMessage` markers to enforce
the recorded thread interleaving.  Each thread blocks until the tape
cursor reaches its segment.  If a thread takes a different code path
(fewer or more proxied calls), it never reaches the expected switch
point, and the other thread waits forever.

**Common causes:**

- Same as tape misalignment — non-deterministic calls changing
  control flow.
- Thread-local state that differs between record and replay.

**How to investigate:**

- Record with `--verbose --stacktraces` to identify the call sites
  around each `THREAD_SWITCH` message.
- Check which thread is blocked and what call it's waiting for.

### 7. Using monitor mode

**What it does:**

`--monitor N` uses Python 3.12's `sys.monitoring` API to write a
`MONITOR` checkpoint for every Python function call and return inside
the sandbox.  During replay, the same callbacks fire and verify each
`MONITOR` message matches.  A mismatch immediately identifies the
exact function where execution diverged.

**Granularity levels:**

| Level | Events | Typical slowdown |
|-------|--------|-----------------|
| 0 | Off (default) — zero overhead, no `sys.monitoring` interaction | None |
| 1 | `PY_START` + `PY_RETURN` — Python function boundaries | ~2–5x |
| 2 | + `CALL` + `C_RETURN` — includes C builtins | ~5–10x |
| 3 | + `LINE` — every source line | ~10–50x |

Level 0 is byte-identical to a trace without the feature.  No tool ID
is claimed, no callbacks registered, no MONITOR messages written.

**Example — recording with monitor:**

```bash
python -m retracesoftware \
    --recording /tmp/trace.bin \
    --monitor 1 \
    -- my_script.py
```

Replay reads the monitor level from the recording's header
automatically — no extra flag needed:

```bash
python -m retracesoftware \
    --recording /tmp/trace.bin
```

**How it helps:**

Standard replay divergence tells you *what* value differed but not
*where* the code path first diverged.  With `--monitor 1`, the replay
raises `ReplayDivergence` at the first function call or return that
differs:

```
ReplayDivergence: monitor divergence: expected 'S:parse_response', got 'S:handle_error'
```

This tells you that after some shared prefix of execution, the
recording entered `parse_response` but the replay entered
`handle_error` — the divergence happened in the code *before* this
call (e.g. a branch that checked a non-deterministic value).

**MONITOR message format:**

Each checkpoint is a compact string:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `S:` | `PY_START` — function entry | `S:module.Class.method` |
| `R:` | `PY_RETURN` — function return | `R:module.Class.method` |
| `C:` | `CALL` — C function call (level 2+) | `C:len` |
| `CR:` | `C_RETURN` — C function return (level 2+) | `CR:len` |
| `L:` | `LINE` — line event (level 3) | `L:module.func:42` |

The writer's handle deduplication means repeated calls to the same
function cost only 2–3 bytes each after the first occurrence.

**Filtering:**

Monitor callbacks automatically filter out retrace's own code:

- C extensions (`utils.observer`, `Gate`, `functional` primitives)
  never fire `PY_START`/`PY_RETURN` events — they are invisible at
  level 1.
- Python functions with `co_filename` inside any `retracesoftware`
  package directory return `sys.monitoring.DISABLE`, permanently
  disabling the callback for that code object at the CPython level.
- A thread-local reentrancy guard prevents recursive monitoring
  during checkpoint writes.
- The `_in_sandbox()` check skips events outside a record/replay
  context.

**In-process testing:**

The `TestRunner` accepts a `monitor=` parameter:

```python
runner = install_for_pytest(modules=["socket"])

def test_dns():
    runner.run(socket.getaddrinfo, "localhost", 80, monitor=1)
```

Or with separate record/replay:

```python
recording = runner.record(do_work, monitor=1)
runner.replay(recording, do_work, monitor=1)
```

**When to use each level:**

- **Level 0** (default): Production recordings.  Zero overhead.
- **Level 1**: First line of investigation when a replay diverges.
  Pinpoints which function call/return mismatched.
- **Level 2**: When the divergence is inside a C extension call
  (level 1 shows the Python caller matches, but the result differs).
- **Level 3**: Last resort.  Pinpoints the exact source line, but
  generates very large traces.

---

## Debugging workflow

### Step 1: Reproduce with diagnostics

```bash
# Record (add --monitor 1 on Python 3.12+ for function-level divergence)
RETRACE_DEBUG=1 python -m retracesoftware \
    --recording /tmp/trace.bin \
    --verbose --stacktraces --monitor 1 \
    -- my_script.py \
    > /tmp/record.log 2>&1

# Replay
RETRACE_DEBUG=1 python -m retracesoftware \
    --recording /tmp/trace.bin \
    --verbose \
    > /tmp/replay.log 2>&1
```

### Step 2: Identify the divergence point

Compare the logs.  The writer log (`record.log`) and reader log
(`replay.log`) should have matching message indices:

```bash
# Writer messages
grep "ObjectWriter" /tmp/record.log | head -50

# Reader messages
grep "ObjectStream" /tmp/replay.log | head -50
```

Find where they stop matching.

### Step 3: Find the call site

If `--stacktraces` was enabled, the recording contains stack frames
for every message.  With `--verbose` replay, the reader prints these:

```
Retrace - ObjectStream[42, 9846] - Consumed STACK - drop: 0
  /path/to/script.py:15
  /path/to/library.py:234
```

This tells you which Python call at message 42 produced the
divergent value.

### Step 4: Check for uncaptured calls

If a C extension call isn't in the TOML configs, it passes through
unrecorded.  During replay it executes live, potentially returning a
different value.

Signs of an uncaptured call:
- The replay produces a different value for something that should be
  deterministic (e.g. a cached file path).
- The message count differs between record and replay (extra or
  missing `SYNC`/`RESULT` pairs).

Fix: Add the call to the appropriate module TOML config.

### Step 5: Check version alignment

Retrace stores checksums of all its own modules in the recording.
On replay, it compares:

```
VersionMismatchError: Module checksums differ:
  retracesoftware/__main__.py: recorded a842e390... current f1b2c3d4...
```

If you've changed retrace code between record and replay, this fires.
The recording also stores `python_version` and `executable` path.

---

## Key error messages reference

| Error | Location | Meaning |
|---|---|---|
| `ReplayDivergence: replay divergence: expected X, got Y` | `messagestream.py` | Checkpoint mismatch — values differ between record and replay |
| `RuntimeError: Can't reading next as unbound pending bind` | `objectstream.cpp` | A `Bind` tag was read but `reader.bind()` was never called before the next `reader()` call |
| `RuntimeError: Could not read: N bytes from tracefile` | `objectstream.cpp` | Trace ended or timeout — recording truncated or misaligned |
| `RuntimeError: Trying to bind when no pending bind` | `objectstream.cpp` | `reader.bind()` called but no `Bind` tag was pending |
| `RuntimeError: object: X already bound` | `writer.h` | Writer tried to bind an object that was already bound |
| `AsyncFilePersister: PID mismatch!` | `persister.cpp` | Frame stamped with wrong PID (fork lifecycle bug) |
| `VersionMismatchError` | `__main__.py` | Module checksums differ between record and replay |
| `ReplayDivergence: replay demux timed out` | `messagestream.py` | Thread couldn't acquire its turn on the tape |
| `ReplayDivergence: monitor divergence: expected X, got Y` | `messagestream.py` | MONITOR checkpoint mismatch — function call/return differs between record and replay |
| `ReplayDivergence: expected MONITOR(X), got ...` | `messagestream.py` | Replay produced a function call that the recording didn't have |
| `ReplayDivergence: unexpected MONITOR(X) during sync` | `messagestream.py` | Recording had function calls that replay didn't replicate |

---

## Testing with debug features

All test suites automatically enable `RETRACE_DEBUG=1` via
`conftest.py`.  Subprocess-based tests in the `retracesoftware` repo
also enable `--stacktraces` on every recording.

To run a specific failing test with maximum diagnostics:

```bash
RETRACE_DEBUG=1 python -m pytest tests/test_fork_exec.py::TestForkTree -v -s --tb=long
```

The `-s` flag prevents pytest from capturing stdout, so you can see
the verbose C++ output interleaved with test output.
