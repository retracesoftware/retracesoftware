# Internals

This section is for maintainers changing Retrace itself.

For user-facing setup, start with [../README.md](../README.md).

## Current Runtime Map

The current Python package is under `src/retracesoftware`.

- `__main__.py` owns the `python -m retracesoftware` CLI. It records target
  commands, replays recordings, lists recorded PIDs, and installs or removes
  the auto-enable hook.
- `autoenable.py` is imported by `retracesoftware_autoenable.pth`. It re-execs
  ordinary Python commands through `python -m retracesoftware` when
  `RETRACE_RECORDING` or `RETRACE_CONFIG` is set.
- `tape.py` creates recording files, writes executable shebangs, stores
  checksums and process metadata, and opens replay readers.
- `install/` patches Python runtime surfaces so external calls cross the
  record/replay boundary.
- `proxy/` defines record/replay boundary behavior, message routing, gates, and
  proxy semantics.
- `modules/` contains stdlib and third-party interception configuration.
- `stream/` and `cpp/stream/` serialize and read trace data.
- `replay/` locates or lazily builds the Go replay binary.
- `control_runtime.py` and `src/retracesoftware/dap/` support debugger control
  and DAP-facing replay behavior.
- `threadid/` provides stable replay thread identifiers.

The Go replay tool lives in `go/` and is responsible for extraction, indexing,
PidFile replay launch, and debug adapter integration.

The VS Code extension lives in `vscode/` and opens `.retrace` recordings through
the Go-owned replay/debug path.

## Architecture References

- [Module Layers](../LAYERS.md)
- [Stream Architecture](../STREAM.md)
- [Thread Replay](../THREAD_REPLAY.md)
- [Cursors](../cursors.md)
- [Debugger Design](../DEBUGGER_DESIGN.md)
- [Debugging Retrace](../DEBUGGING.md)

## Component Contracts

Before editing a component, read its local `AGENTS.md`. If the component has a
`DESIGN.md`, treat it as the behavior contract for that layer.

Important current contracts:

- `src/retracesoftware/proxy/AGENTS.md`
- `src/retracesoftware/proxy/DESIGN.md`
- `src/retracesoftware/install/AGENTS.md`
- `src/retracesoftware/modules/AGENTS.md`
- `src/retracesoftware/stream/AGENTS.md`
- `src/retracesoftware/protocol/AGENTS.md`
- `src/retracesoftware/dap/AGENTS.md`
- `go/AGENTS.md`
- `vscode/AGENTS.md`
- `tests/AGENTS.md`
- `dockertests/AGENTS.md`

## Maintainer Rule Of Thumb

Replay should never touch live external state. During record, external calls
execute and are written to the trace. During replay, those calls must be served
from the trace. If a replay bug is fixed by letting replay call the live
filesystem, network, clock, RNG, subprocess layer, or debugger control path, the
fix is in the wrong layer.
