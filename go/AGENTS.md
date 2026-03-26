# Go Replay Tooling

This directory owns the Go replay binary, extraction/index/workspace tooling,
and a large part of the DAP/control-plane contract. Changes here can break the
CLI, pidfile workflows, debugger behavior, or alignment with the Python DAP
path even when Python runtime code is unchanged.

## Current Core Files

- `cmd/replay/main.go`
  CLI entrypoint for extraction, indexing, workspace generation, direct replay,
  roundtrip, and DAP proxy mode.
- `replay/proxy.go`
  Go-owned DAP endpoint that maps client requests onto replay/debugger calls.
- `replay/controlproto.go`
  Control-message schema, stop payload parsing, cursor/message-index fields.
- `replay/codec.go`
  DAP framing helpers and wire-level request/response/event encoding.
- `replay/debugger.go`, `replay/engine.go`, `replay/cursor.go`
  Replay navigation, stop-state handling, cursor semantics, and query engine.
- `replay/index.go`, `replay/linearize.go`, `replay/pidrunner.go`
  Recording extraction, pidfile generation, and replay startup plumbing.

## Mental Model

- The Go replay binary is not a thin wrapper. It defines user-visible CLI and
  debugger behavior for extracted recordings and pidfiles.
- Recording mode and pidfile mode should stay aligned semantically even when
  their plumbing differs.
- DAP event ordering, stop reasons, `message_index`, and cursor payloads are
  externally visible contract, not logging details.
- The Go DAP path and the Python DAP path should stay conceptually aligned.
- Extraction/index/workspace output is part of the developer workflow contract;
  layout drift can break downstream tooling and tests.
- The `roundtrip` path in `cmd/replay/main.go` is also part of the tooling
  contract; CLI or argument drift there can break end-to-end debugging loops.

## High-Risk Areas

- CLI flag or output drift in `cmd/replay/main.go`.
- DAP request/response ordering and payload shape in `proxy.go`.
- DAP framing and message codec behavior in `codec.go`.
- `ControlStopResult`, cursor parsing, and `message_index` semantics.
- Divergence between recording replay, pidfile replay, and DAP replay paths.
- Extraction or linearization changes that alter shebangs, pidfile layout, or
  index/workspace expectations.
- `roundtrip` command-line semantics and how they align with Python replay CLI.
- Cross-language drift between Go replay/control behavior and Python tests or
  docs that still assume the old contract.

## Working Rules

- Treat CLI flags, output formats, and DAP/control payloads as compatibility
  boundaries. If they change, update the matching tests in the same diff.
- If a DAP change affects event ordering or stop semantics, compare it against
  the Python-side expectations in `src/retracesoftware/dap/` and
  `tests/test_stdio_replay.py`.
- Keep pidfile replay and recording replay behavior aligned unless the
  difference is deliberate and documented.
- Prefer focused Go tests in `go/replay/*_test.go` for replay/control changes.
- If a change affects extraction or workspace generation, verify the resulting
  filesystem layout and index metadata, not just the happy-path command output.

## Build And Test

- Build the replay binary:
  `cd go && go build -o ../.retrace-replay-bin ./cmd/replay`
- Run Go tests:
  `cd go && go test ./...`

## References

- `go/cmd/replay/main.go`
- `go/replay/proxy.go`
- `go/replay/controlproto.go`
- `go/replay/codec.go`
- `go/replay/debugger.go`
- `go/replay/index.go`
- `go/replay/linearize.go`
