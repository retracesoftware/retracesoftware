# Test Suite

This directory is not one flat test bucket. It is split by subsystem, and good
changes should usually add or update tests in the narrowest responsible area.

## Layout

- `tests/install/`
  Runtime wiring, import hooks, startup/shutdown, stdlib/external patching.
- `tests/proxy/`
  Boundary behavior, callback semantics, replay divergence rules.
- `tests/stream/`
  Binding, transport, reader/writer parity, backpressure, demux.
- `tests/replay/`
  Replay execution behavior.
- `tests/functional/`, `tests/utils/`
  Lower-level utility behavior.
- `tests/scripts/`
  Subprocess targets and helper scripts used by end-to-end and control/runtime
  tests.
- top-level tests such as `test_stdio_replay.py`, `test_record_replay.py`
  cover cross-layer contracts and CLI/control protocol behavior.

## Failure Classification

Before proposing a fix, classify the failure:

- Product bug
  Real runtime, replay, protocol, or contract regression.
- Environment / sandbox
  Socket bind restrictions, filesystem/network policy, or other local runner
  constraints that do not reflect product behavior.
- Setup / packaging
  Missing build artifacts, wrong interpreter, stale Go binary, wrong editable
  install, or test runner environment mismatch.
- Test drift
  Tests or helpers still assume old CLI flags, old event ordering, or outdated
  helper behavior.

Do not treat all failures as product bugs automatically.

## Current Examples And Recent Hot Spots

These are current examples, not permanent truth. Refresh them when the failure
mix changes.

- Binding/materialization failures such as `ExpectedBindingCreate`
  or runtime errors matching `expected BindingCreate during bind`
  usually belong to stream/protocol/proxy contracts.
- Legacy tests or helpers still using `--raw` instead of current `--format`
  handling are test/CLI drift.
- Extra protocol events ahead of expected events are often contract drift, not
  random flakiness.
- `breakpoint_hit` ordering drift or `set_backstop` mismatches are usually
  control-protocol contract issues, not generic flakiness.
- Sandbox-only socket or bind failures should be separated from unsandboxed
  product failures before recommending architectural changes.
- Shutdown hangs after user code completes are install/run lifecycle bugs.

## Working Rules

- Prefer `tests/helpers.py` for shared record/replay helpers instead of copying
  ad hoc subprocess command construction.
- If a test launches a subprocess or records/replays a helper script, check
  `tests/scripts/` before creating a new ad hoc target.
- When changing CLI or protocol semantics, update the relevant tests in the same
  diff or call out the intentional drift explicitly.
- If a failure is environment-specific, say so clearly and avoid overfitting the
  product code to the local runner.
- Add the narrowest regression test that reproduces the real bug.
- For cross-layer failures, identify the first owning layer before choosing
  which test bucket to extend.

## References

- `tests/helpers.py`
- `tests/scripts/`
- `tests/test_stdio_replay.py`
- `tests/test_record_replay.py`
- `tests/install/stdlib/test_record_shutdown_threadpool_hang_regression.py`
- `tests/stream/test_persister.py`
