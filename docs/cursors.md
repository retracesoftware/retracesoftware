# Cursors

A **cursor** uniquely identifies a point in a recorded Python execution. The
replay system uses cursors to navigate forward, backward, and to breakpoints
without re-executing from the start of the trace every time.

## Anatomy of a cursor

A cursor has three fields:

| Field | Type | Description |
|---|---|---|
| `thread_id` | varies | Identifies the thread (see below) |
| `function_counts` | `[]int` | Per-frame call counts from root to leaf |
| `f_lasti` | `*int` (optional) | Bytecode offset in the leaf frame |

`function_counts` is the key concept. It is a stack of integers, one per
Python call frame that is being tracked. Each integer records how many
`PY_START` events the parent frame has seen at that depth. Together with
`f_lasti` they pinpoint an exact bytecode instruction inside a specific
invocation of a specific function.

### Thread IDs

`thread_id` is not necessarily a single integer. During live recording,
it may be the OS thread ID (`uint64`), but in the replay engine it is a
**stable thread ID** — a tuple of numbers that uniquely identifies a thread
across replays regardless of OS-assigned IDs. The stable form ensures that
cursors serialised from one replay can be used to navigate in another replay
of the same trace without ID mismatches.

### Example

```python
def foo():        # line 1
    a = 'bar'     # line 2
    print(a)      # line 3

x = 10            # line 4
y = 20            # line 5
z = x + y         # line 6
foo()             # line 7
print(f"{z}")     # line 8
```

Execution events (simplified, assuming the module is the root frame):

| Event | `cursor_stack` | Notes |
|---|---|---|
| Module `PY_START` | `[0]` | Root frame pushed with count 0 |
| Lines 1-6 execute | `[0]` | No calls, stack unchanged |
| `foo()` `PY_START` | `[1, 0]` | Parent count 0→1, child pushed with 0 |
| `a = 'bar'` executes | `[1, 0]` | Assignment, no call |
| `print(a)` `PY_START` | `[1, 1, 0]` | foo's count 0→1, print pushed |
| `print(a)` `PY_RETURN` | `[1, 1]` | print frame popped |
| `foo()` `PY_RETURN` | `[1]` | foo frame popped |
| `print(f"{z}")` `PY_START` | `[2, 0]` | Module count 1→2, print pushed |

A cursor for "stopped at line 2 inside `foo()`" would be:

```
thread_id: (0, 1)     # stable thread ID (tuple)
function_counts: [1, 0]
f_lasti: 2            # bytecode offset of STORE_FAST for a = 'bar'
```

## How call counts are tracked (C++)

The C++ extension (`cpp/cursor/`) hooks into `sys.monitoring` (Python 3.12+)
and registers callbacks for these events:

- **`PY_START`** — Increments the parent frame's `call_count`, pushes a new
  `CursorEntry{0}` for the callee, then runs `check_watches(start)`.
- **`PY_RETURN`** — Runs `check_watches(on_return)` (before pop), pops the
  top frame, then runs `check_watches(start)`.
- **`PY_UNWIND`** — Same as `PY_RETURN` but fires the `unwind` slot.
- **`JUMP`** — Fires `check_watches(backjump)` on backward jumps (loops).

The state lives in a per-thread `ThreadCallCounts` object, which holds:
- `cursor_stack: vector<CursorEntry>` — the live call-count stack
- `watches: vector<WatchState>` — armed one-shot watches
- `suspend_depth` — when > 0, all events are ignored (used by `disable_for`)

### Root frame tracking

When the `cursor_stack` is empty (the very first `PY_START`), the extension
inspects the CPython internal frame chain to figure out the root count. This
handles cases where the call counter is installed mid-execution and the first
tracked frame is not the true root of the process.

## WatchState

A `WatchState` watches for a target `function_counts` and fires one-shot
callbacks when execution reaches specific events at that position.

### Slots

| Slot | Fires when | Match rule |
|---|---|---|
| `on_start` | `PY_START`, `PY_RETURN` (after pop), `PY_UNWIND` (after pop) | Progressive prefix match, then exact depth |
| `on_return` | `PY_RETURN` (before pop) | Exact match (depth + all counts) |
| `on_unwind` | `PY_UNWIND` (before pop) | Exact match |
| `on_backjump` | `JUMP` (backward) | Exact match |
| `on_overshoot` | When `on_start` detects the target was passed | Fires instead of `on_start` |

### `fire_start` matching algorithm

`on_start` uses a progressive prefix-matching strategy via
`start_match_prefix_`:

1. For each level `i` from `start_match_prefix_` to `target.size()`:
   - If `cursor_stack[i] < target[i]` → not there yet, return (keep watching)
   - If `cursor_stack[i] > target[i]` → overshot, fire `on_overshoot`, remove watch
   - If equal → advance `start_match_prefix_` (this level is locked in)
2. Once all levels prefix-matched, check `stack.size() == target.size()`:
   - If deeper → not there yet (we're inside a child call), keep watching
   - If exact → **fire** `on_start`

The prefix is sticky — once a level matches, it is not re-checked. This makes
the match O(1) amortised across all `check_watches` calls for a given watch.

### `fire_exact` matching

`on_return`, `on_unwind`, and `on_backjump` use a simpler exact-match: the
`cursor_stack` must have the same length as `target` and every count must
match. This fires at the exact frame depth before the frame is popped.

## Python layer (`retracesoftware.cursor`)

The Python module provides two levels of API:

### CallCounter (high-level)

```python
from retracesoftware.cursor import CallCounter

cc = CallCounter()
tc = cc()              # ThreadCallCounts for current thread
tc.add_watch(counts, on_return=cc.disable_for(callback))
with tc:
    target()
```

`CallCounter` owns the `sys.monitoring` hooks. Calling it returns the
`ThreadCallCounts` for the current thread. The context manager protocol on
`ThreadCallCounts` resets the call-count stack on entry and exit.

### Module-level functions (legacy)

```python
from retracesoftware import cursor

cursor.install_call_counter()
cursor.watch((1, 0), on_start=callback, on_missed=error_callback)
snap = cursor.cursor_snapshot()
```

`cursor.watch()` is an alias for `cursor.add_watch()`, which arms a
`WatchState` on the current thread's `ThreadCallCounts`. The `on_missed`
parameter maps to the C++ `on_overshoot` slot.

### Cursor dataclass

```python
@dataclass(frozen=True)
class Cursor:
    thread_id: int | tuple
    function_counts: tuple
    f_lasti: int | None = None
```

`cursor_snapshot()` captures the current position as a `Cursor`. The
`to_dict()` / `from_dict()` methods serialise it for the control protocol.

## Go layer (`replay.Cursor`)

The Go `Cursor` type wraps a `Location` and an optional cached `*Replay`. It
provides DAP navigation methods:

| Method | Target counts | Watch slot |
|---|---|---|
| `Next` | Same depth, advance by instruction until line changes | Instruction stepping |
| `StepInto` | `[..., N+1, 0]` — one level deeper | `on_start` at callee entry |
| `Return` | Same depth via `RunToReturn` | `on_return` at current frame |
| `Previous` | Replay from snapshot, walk forward to last different line | Instruction iteration |
| `StepBackInto` | Replay from `ClosestBeforeReturn` snapshot | `on_return` |

### StepInto target calculation

Given current `function_counts = [a, b, c]`:

```
target = [a, b, c+1, 0]
```

The parent's count is incremented (because `PY_START` bumps `cursor_stack.back()`)
and a zero is appended for the child frame. This matches the exact stack state
at the callee's `PY_START` event.

**Why not `[a, b, c+1]`?** That would match at the same depth — which only
happens *after* the callee returns, not when it starts. The `fire_start` exact
depth check (`n == target_size`) would skip the callee entry and fire when the
stack pops back to the caller.

## Control protocol

The Go replay sends commands to the Python control runtime over a Unix socket.
Cursor-related commands:

| Command | Python intent | Description |
|---|---|---|
| `run_to_cursor` | `StopAtCursor` | Run until `function_counts` (and optionally `f_lasti`) match |
| `run_to_return` | `RunToReturn` | Run until the target frame returns |
| `next_instruction` | `NextInstruction` | Execute one bytecode instruction |

### `run_to_cursor` two-phase precision

When `f_lasti` is specified in the target cursor:

1. **Phase 1** — `cursor.watch(counts, on_start=_on_counts_match)` waits for
   the `function_counts` to match.
2. **Phase 2** — Once counts match, a temporary `INSTRUCTION` monitor is
   installed on the leaf frame's code object. It fires when
   `offset == f_lasti`, giving bytecode-level precision.

When `f_lasti` is `None`, only phase 1 is used (function-entry stop).

Both paths include `on_missed` to detect overshoot and report `"eof"` instead
of hanging indefinitely.

## `disable_for`

`disable_for(fn)` returns a C wrapper that increments `suspend_depth` before
calling `fn` and decrements it after. While suspended, all monitoring
callbacks are no-ops. This prevents the framework's own function calls from
perturbing the call-count stack.

Every callback registered with `sys.monitoring` or passed to `add_watch`
should be wrapped with `disable_for` to keep counts accurate.

## Thread handling

- `CallCounter.on_thread_switch` — A callback fired when the C++ layer
  detects that the current `PyThreadState` has changed between consecutive
  monitoring events. Used by the replay to track thread interleaving.
- `callback_on_thread(thread_id, callback)` — Schedules a callback to fire on
  the next monitored event (any of `PY_START`/`PY_RETURN`/`PY_UNWIND`/`JUMP`)
  on a specific thread. Used to switch control to a target thread during
  replay navigation.

## Snapshot-based replay

Navigation methods on `Cursor` use `SnapshotProvider.ClosestBeforeCall` to
find the nearest checkpoint *before* the target position, fork a replay from
that checkpoint, and run forward to the target. This avoids replaying from the
start of the trace for every navigation, keeping step/continue latency low.

The `Replay` object owns the forked Python subprocess and communicates via the
control protocol. When a `Cursor` is created from a navigation result, it may
cache the `Replay` for subsequent queries (stack, locals, source location).
Navigation methods "steal" the cache (`takeReplay`) so the old cursor remains
valid but uncached.
