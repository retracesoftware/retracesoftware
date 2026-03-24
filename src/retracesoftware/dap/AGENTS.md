# DAP Layer

This directory implements Retrace's debugger/control-plane behavior. It is not
normal replay data-path code. DAP messages, control sockets, debugger stepping,
and trace-reader control flow must stay invisible to retrace recording and must
not interfere with replay correctness.

## Current Core Files

- `adapter.py`
  Python DAP adapter, request handling, lifecycle, breakpoints, stepping,
  scopes, variables, source loading.
- `debug/hooks.py`
  Breakpoint, pause/resume, and stepping hooks using `sys.monitoring` on 3.12+
  or `sys.settrace` on older versions.
- `debug/cursor.py`
  Global replay cursor state used for debugger navigation.
- `protocol/dispatch.py`, `protocol/framing.py`, `protocol/types.py`
  DAP message dispatch and wire protocol helpers.
- `replay/gate.py`
  Control-plane gate disabling helpers.
- `replay/transport.py`
  Trace reader / replay transport helpers.

The Go side in `go/replay/` is closely related. Changes here often require
checking the Go DAP proxy and replay tooling too.

## Mental Model

- DAP/control-plane I/O is not part of the user's recorded execution.
- Debugger socket I/O, control messages, and trace-reader plumbing must bypass
  retrace interception or the debugger will observe and perturb itself.
- Paused-state behavior, stepping, and cursor advancement must preserve replay
  truth rather than emulate a live debugger loosely.
- Python and Go DAP paths should stay conceptually aligned even if they are not
  identical implementations.
- The Python DAP path currently assumes a single logical debugger thread id
  (`THREAD_ID = 1`) and a global monotonic replay cursor.
- Inspector references are pause-scoped. `Inspector.invalidate()` runs on
  resume, so scopes/variables/evaluate results must not assume long-lived
  frame-backed object identity.

## High-Risk Areas

- Anything that adds I/O without gate bypass.
- Pause/resume and step-mode state transitions.
- Cursor advancement, reset behavior, and source/line bookkeeping.
- Breakpoint condition evaluation and frame selection.
- `sys.monitoring` vs `sys.settrace` backend differences.
- Trace reader reopen/offset behavior around fork and replay navigation.
- Variable/scopes/evaluate behavior that may expose stale paused-state objects.
- Reconnect/fork behavior that changes which transport or socket the debugger uses.
- Adapter reconnect-after-fork behavior is part of the design; child replay
  processes need their own transport/file description state.

## Working Rules

- Treat control-plane I/O as special. Keep it outside normal retrace boundary
  semantics.
- If you change stepping, pause, or cursor behavior, explain how replay order
  and stop points are affected.
- Do not assume debugger-visible state can outlive resume. Be careful with
  references tied to a paused frame.
- If a DAP change relies on Go replay behavior, verify the matching assumptions
  in `go/replay/`.
- Preserve low-overhead behavior on Python 3.12+ by respecting the existing
  `sys.monitoring` design where possible.
- Assume paused-frame state is fragile. After resume, references derived from a
  paused frame may no longer be valid.

## Build And Test

- Python-side DAP/replay tests live in the main Python test suite.
- Go-side debugger/replay tests:
  `cd go && go test ./...`
- When debugging protocol issues, check both the Python DAP layer and the Go
  replay proxy behavior.

## References

- `src/retracesoftware/dap/adapter.py`
- `src/retracesoftware/dap/debug/hooks.py`
- `src/retracesoftware/dap/debug/cursor.py`
- `src/retracesoftware/dap/protocol/dispatch.py`
- `src/retracesoftware/dap/replay/gate.py`
- `src/retracesoftware/dap/replay/transport.py`
- `go/replay/proxy.go`
- `docs/cursors.md`
- `docs/DEBUGGING.md`
